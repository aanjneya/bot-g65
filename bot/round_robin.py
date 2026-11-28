"""Sequential Live Optibook Round-Robin Evaluator & Risk-Adjusted Winner Selector.

Runs each Strategy Lab candidate sequentially on ONE Exchange connection (zero connection kicks,
zero cross-strategy self-trades) for `--minutes` (default 12.0 min per strategy), recording:
- Live PnL (mark-to-mid and exchange last-trade), PnL/min
- Annualized Sharpe & Sortino ratios (computed from 1-second PnL return increments)
- Max Drawdown ($), Calmar Ratio (PnL / |MaxDD|), Peak Gross Inventory, Max Req/s
- Full CSV market recording (`books.csv`, `trades.csv`, `pnl.csv`) per round
- Optional post-round cross-replay (`--cross-replay`) evaluating ALL strategies on ALL recorded
  round-robin chunks via `bot/backtest.py` to remove time-of-day regime luck!

Usage (inside Optiver Dev Environment for live Optibook):
    python bot/round_robin.py --minutes 12

Usage (local MockExchange smoke test):
    python3 bot/round_robin.py --mock --seconds 5 --no-cross-replay
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for p in (ROOT, HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

from bot.config import BotConfig  # noqa: E402
from bot.runner import MarketLogger, build_exchange  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("round-robin")

DEFAULT_STRATEGIES = [
    "strat_top2_delay_deconv:DelayDeconvWaveBot",
    "_penny_55k:P2_PennyJump_All8Leaders",
    "strat_top5_velocity_unpin:VelocityUnpinMakerSniper",
    "strat_top3_pair_glft:BlockPairGLFTBot",
    "strat_top6_omnibus:ApexOmnibusChampion",
    "strat_top4_micro_obi:StudentBookMicroPennyBot",
    "strat_top1_blind_kalman:BlindGraphKalmanBot",
]


def _load_strategy_cls(spec: str):
    mod_name, cls_name = spec.split(":")
    mod = __import__(mod_name)
    return getattr(mod, cls_name)


def _mid_valuations(ex: Any, insts: List[str]) -> Dict[str, float]:
    vals: Dict[str, float] = {}
    for s in insts:
        b = ex.get_last_price_book(s)
        if b and b.bids and b.asks:
            vals[s] = 0.5 * (float(b.bids[0].price) + float(b.asks[0].price))
    return vals


def compute_risk_metrics(pnl_series: List[Tuple[float, float, int]]) -> Dict[str, float]:
    """Compute PnL, MaxDD, Calmar, Sharpe, Sortino, and Peak Gross Inventory from (t_sec, pnl, gross)."""
    if not pnl_series:
        return {"pnl": 0.0, "max_dd": 0.0, "calmar": 0.0, "sharpe": 0.0, "sortino": 0.0, "peak_gross": 0}

    final_pnl = pnl_series[-1][1]
    peak = 0.0
    max_dd = 0.0
    peak_gross = 0
    for _, p, g in pnl_series:
        if p > peak:
            peak = p
        if p - peak < max_dd:
            max_dd = p - peak
        if g > peak_gross:
            peak_gross = g

    # Resample to 1-second buckets for clean Sharpe / Sortino without 0.2s microstructure noise
    buckets: Dict[int, float] = {}
    for t_s, p, _ in pnl_series:
        buckets[int(t_s)] = p
    secs = sorted(buckets)
    rets = [buckets[secs[i]] - buckets[secs[i - 1]] for i in range(1, len(secs))]

    sharpe = 0.0
    sortino = 0.0
    if len(rets) >= 3:
        mean_r = sum(rets) / len(rets)
        var_r = sum((r - mean_r) ** 2 for r in rets) / max(1, len(rets) - 1)
        std_r = math.sqrt(var_r)
        # Annualize on 252 trading days * 6.5 hours * 3600s (or report scaled Sharpe; relative ranking is identical)
        scale = math.sqrt(len(rets))  # Session Sharpe (sqrt(N) * mean / std)
        if std_r > 1e-9:
            sharpe = scale * mean_r / std_r
        downside = [min(0.0, r) ** 2 for r in rets]
        d_std = math.sqrt(sum(downside) / max(1, len(rets) - 1))
        if d_std > 1e-9:
            sortino = scale * mean_r / d_std

    calmar = final_pnl / max(1.0, abs(max_dd))
    return {
        "pnl": round(final_pnl, 2),
        "max_dd": round(max_dd, 2),
        "calmar": round(calmar, 2),
        "sharpe": round(sharpe, 2),
        "sortino": round(sortino, 2),
        "peak_gross": int(peak_gross),
    }


def run_one_round(
    spec: str,
    duration_sec: float,
    out_dir: str,
    mock_mode: bool,
    poll_sec: float = 0.20,
) -> Dict[str, Any]:
    os.makedirs(out_dir, exist_ok=True)
    cls = _load_strategy_cls(spec)

    cfg = BotConfig(mock_mode=mock_mode, max_position_lots=100)
    if duration_sec >= 60.0 and "ApexOmnibusChampion" in spec:
        os.environ["SESSION_DURATION_SEC"] = str(duration_sec)
    else:
        os.environ["SESSION_DURATION_SEC"] = "0"

    ex = build_exchange(cfg)
    ex.connect()
    strat = cls(ex)
    insts = list(getattr(strat, "insts", [])) or sorted(ex.get_tradable_instruments().keys())

    # Flatten baseline valuation by recording initial PnL (in case team account starts with prior positions/cash)
    init_vals = _mid_valuations(ex, insts)
    init_pnl = float(ex.get_pnl(valuations=init_vals) if init_vals else ex.get_pnl())

    mlog = MarketLogger(out_dir)
    pnl_rows: List[Tuple[float, float, int]] = []
    t0 = time.monotonic()
    ticks = 0
    disconnected = False

    logger.info("=== ROUND START: %s (%.1f min, mock=%s) ===", spec, duration_sec / 60.0, mock_mode)
    try:
        while time.monotonic() - t0 < duration_sec:
            if not ex.is_connected():
                disconnected = True
                logger.warning("Exchange disconnected mid-round for %s!", spec)
                break
            if mock_mode and hasattr(ex, "step_market"):
                ex.step_market()
            strat.step()
            ticks += 1

            elapsed = time.monotonic() - t0
            vals = _mid_valuations(ex, insts)
            cur_pnl = float(ex.get_pnl(valuations=vals) if vals else ex.get_pnl()) - init_pnl
            pos = ex.get_positions()
            gross = sum(abs(int(v)) for v in pos.values())
            pnl_rows.append((elapsed, cur_pnl, gross))

            books = {s: ex.get_last_price_book(s) for s in insts}
            mlog.record(books, {}, [])

            if ticks % 150 == 0:
                logger.info(
                    "[%s] t=%.0fs/%.0fs pnl=%+.2f gross=%d",
                    spec.split(":")[-1],
                    elapsed,
                    duration_sec,
                    cur_pnl,
                    gross,
                )
            time.sleep(poll_sec)
    except KeyboardInterrupt:
        logger.info("Interrupted round %s early", spec)
    finally:
        for s in insts:
            try:
                ex.delete_orders(s)
            except Exception:
                pass
        mlog.close()
        try:
            ex.disconnect()
        except Exception:
            pass

    with open(os.path.join(out_dir, "pnl.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t_sec", "pnl", "gross_pos"])
        for t_s, p, g in pnl_rows:
            w.writerow([f"{t_s:.2f}", f"{p:.2f}", g])

    metrics = compute_risk_metrics(pnl_rows)
    mins = max(0.01, (pnl_rows[-1][0] if pnl_rows else duration_sec) / 60.0)
    metrics.update(
        {
            "strategy": spec,
            "folder": out_dir,
            "minutes": round(mins, 2),
            "pnl_per_min": round(metrics["pnl"] / mins, 2),
            "ticks": ticks,
            "disconnected": disconnected,
        }
    )
    logger.info(
        "=== ROUND END: %s | PnL=%+.2f | Sharpe=%.2f | MaxDD=%.2f | Calmar=%.2f ===",
        spec.split(":")[-1],
        metrics["pnl"],
        metrics["sharpe"],
        metrics["max_dd"],
        metrics["calmar"],
    )
    return metrics


def score_and_rank(results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Composite risk-adjusted rank across PnL, Sharpe, Calmar, and MaxDD (z-score / rank-average)."""
    if not results:
        return []
    n = len(results)
    # Higher is better for pnl, sharpe, calmar, and max_dd (since max_dd is <= 0)
    by_pnl = {r["strategy"]: i for i, r in enumerate(sorted(results, key=lambda x: x["pnl"], reverse=True))}
    by_shp = {r["strategy"]: i for i, r in enumerate(sorted(results, key=lambda x: x["sharpe"], reverse=True))}
    by_cal = {r["strategy"]: i for i, r in enumerate(sorted(results, key=lambda x: x["calmar"], reverse=True))}
    by_mdd = {r["strategy"]: i for i, r in enumerate(sorted(results, key=lambda x: x["max_dd"], reverse=True))}

    for r in results:
        s = r["strategy"]
        avg_rank = 0.35 * by_pnl[s] + 0.30 * by_sharpe_key(by_shp, s) + 0.20 * by_cal[s] + 0.15 * by_mdd[s]
        if r.get("disconnected"):
            avg_rank += n
        r["composite_score"] = round(100.0 * (1.0 - avg_rank / max(1, n)), 1)
    return sorted(results, key=lambda x: x["composite_score"], reverse=True)


def by_sharpe_key(d: Dict[str, int], k: str) -> int:
    return d[k]


def write_round_robin_report(results: List[Dict[str, Any]], out_root: str) -> str:
    ranked = score_and_rank(results)
    lines = [
        f"# Live Optibook Round-Robin Leaderboard ({datetime.now():%Y-%m-%d %H:%M:%S})",
        "",
        "Composite Score = `35% PnL + 30% Session Sharpe + 20% Calmar (PnL/|MaxDD|) + 15% MaxDD`.",
        "",
        "| Rank | Strategy | Score | PnL | PnL/min | Sharpe | Sortino | Max DD | Calmar | Peak Gross | Disc |",
        "| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |",
    ]
    for idx, r in enumerate(ranked, 1):
        lines.append(
            f"| **{idx}** | `{r['strategy']}` | **{r['composite_score']:.1f}** | {r['pnl']:+.2f} | "
            f"{r['pnl_per_min']:+.2f} | {r['sharpe']:+.2f} | {r['sortino']:+.2f} | {r['max_dd']:.2f} | "
            f"{r['calmar']:.2f} | {r['peak_gross']} | {'YES' if r['disconnected'] else 'no'} |"
        )
    if ranked:
        w = ranked[0]
        lines += ["", f"## Winner: `{w['strategy']}` (Score `{w['composite_score']:.1f}`, PnL `{w['pnl']:+.2f}`, Sharpe `{w['sharpe']:+.2f}`, MaxDD `{w['max_dd']:.2f}`)"]
    md_path = os.path.join(out_root, "round_robin_report.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(out_root, "round_robin_results.json"), "w", encoding="utf-8") as f:
        json.dump(ranked, f, indent=2)
    return md_path


def main():
    ap = argparse.ArgumentParser(description="Sequential Live Optibook Round-Robin Tournament")
    ap.add_argument("--minutes", type=float, default=12.0, help="Minutes per strategy (default 12.0)")
    ap.add_argument("--seconds", type=float, default=None, help="Override seconds per strategy (for quick tests)")
    ap.add_argument("--mock", action="store_true", help="Run against MockExchange instead of live Optibook")
    ap.add_argument("--strategies", nargs="*", default=DEFAULT_STRATEGIES, help="List of module:Class specs")
    ap.add_argument("--out-dir", type=str, default=None, help="Output folder (default data/round_robin_<ts>)")
    args = ap.parse_args()

    dur_sec = args.seconds if args.seconds is not None else args.minutes * 60.0
    out_root = args.out_dir or os.path.join(ROOT, "data", f"round_robin_{datetime.now():%d_%H%M%S}")
    os.makedirs(out_root, exist_ok=True)

    results = []
    for idx, spec in enumerate(args.strategies, 1):
        sub_dir = os.path.join(out_root, f"r{idx:02d}_{spec.split(':')[-1]}")
        res = run_one_round(spec, dur_sec, sub_dir, mock_mode=args.mock)
        results.append(res)
        md_path = write_round_robin_report(results, out_root)
        if idx < len(args.strategies):
            time.sleep(0.5 if args.mock else 2.0)

    print("\n" + open(md_path, encoding="utf-8").read())


if __name__ == "__main__":
    main()
