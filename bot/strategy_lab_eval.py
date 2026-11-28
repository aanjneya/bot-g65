"""Fast, bit-exact 24-chunk parallel Optibook Strategy Lab evaluator & anti-overfitting validator.

Uses the EXACT `SimClock`, `SimExchange`, `Disconnected`, `make_strategy`, `STEP`, and `mids`
from `bot/backtest.py` without modifying simulation rules, while:
1) Strictly selecting the 24 non-overlapping real Optibook chunks (606.5 min, 588,175 trades),
   excluding synthetic MOCK_MODE runs, `/snapshots/` prefixes, and overlapping restarts.
2) Caching parsed event streams in `/tmp/optibook_24chunks_cache_v1.pkl` so 24-chunk evaluations
   run in ~1.5-3s across CPU cores without writing untracked files into git-tracked `data/`.
3) Computing comprehensive anti-overfitting metrics:
   - Chronological Train (Chunks 0..11) vs Out-Of-Sample Test (Chunks 12..23)
   - 3-Era Chronological Split (Era 1: Chunks 0..7, Era 2: Chunks 8..15, Era 3: Chunks 16..23)
   - Intra-chunk Half 1 vs Half 2 consistency
   - Chunk Win Rate (out of 24), Worst Chunk PnL, Worst Intra-Chunk Drawdown, PnL/MaxDD Calmar
   - Per-Stock PnL, Fills (Passive vs Aggressive), 1s/5s Markouts, Max Req/s, Zero Disconnects
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import multiprocessing as mp
import pickle
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if HERE not in sys.path:
    sys.path.insert(0, HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import backtest as bt  # noqa: E402
from wand import load as wand_load  # noqa: E402

CACHE_PATH = "/tmp/optibook_24chunks_cache_v1.pkl"
ALL_STOCKS = ("AMD", "AVGO", "CRWV", "MU", "NBIS", "NVDA", "SNDK", "VRT")


def _is_mock_dir(folder: str) -> bool:
    rlog = os.path.join(folder, "runner.log")
    if os.path.exists(rlog):
        try:
            with open(rlog, encoding="utf-8", errors="ignore") as f:
                head = f.read(2048)
            if "MOCK_MODE=true" in head or "mock=True" in head:
                return True
        except OSError:
            pass
    return False


def discover_24_chunks(root: str = ROOT) -> List[Dict[str, Any]]:
    """Deterministically select the 24 strictly non-overlapping real Optibook chunks."""
    cands = sorted(
        set(
            os.path.dirname(os.path.abspath(p))
            for p in glob.glob(os.path.join(root, "**", "books.csv"), recursive=True)
            if "/snapshots/" not in p and "/backups/" not in p
        )
    )
    raw_items = []
    for d in cands:
        if _is_mock_dir(d):
            continue
        bpath = os.path.join(d, "books.csv")
        tpath = os.path.join(d, "trades.csv")
        if not os.path.exists(tpath) or os.path.getsize(bpath) == 0:
            continue
        with open(bpath, encoding="utf-8") as f:
            f.readline()
            first_line = f.readline()
            if not first_line:
                continue
            first_ts = first_line.split(",")[0]
        with open(bpath, "rb") as f:
            f.seek(max(0, os.path.getsize(bpath) - 8192))
            last_ts = f.read().decode("utf-8", "ignore").strip().splitlines()[-1].split(",")[0]
        t0 = datetime.fromisoformat(first_ts).timestamp()
        t1 = datetime.fromisoformat(last_ts).timestamp()
        mins = (t1 - t0) / 60.0
        if mins < 1.0:
            continue
        with open(tpath, encoding="utf-8") as f:
            ntrades = max(0, sum(1 for _ in f) - 1)
        rel = os.path.relpath(d, root)
        raw_items.append({"folder": d, "rel": rel, "t0": t0, "t1": t1, "mins": mins, "trades": ntrades})

    raw_items.sort(key=lambda x: (x["t0"], -x["mins"]))
    selected: List[Dict[str, Any]] = []
    last_end = -1.0
    for item in raw_items:
        # Allow tiny <= 2s clock-flush jitter between consecutive chunks, reject genuine overlaps
        if item["t0"] >= last_end - 2.0:
            selected.append(item)
            last_end = item["t1"]
    return selected


def build_or_load_cache(force: bool = False) -> List[Dict[str, Any]]:
    if not force and os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "rb") as f:
                data = pickle.load(f)
            if isinstance(data, list) and len(data) == 24:
                return data
        except Exception:
            pass

    meta = discover_24_chunks(ROOT)
    chunks = []
    for idx, m in enumerate(meta):
        books, trades = wand_load(m["folder"])
        events = []
        for inst, snaps in books.items():
            for t, b, a in snaps:
                events.append((t, 0, inst, b, a))
        for inst, ts_list in trades.items():
            for t, p, v in ts_list:
                events.append((t, 1, inst, p, v))
        events.sort(key=lambda e: (e[0], e[1]))
        t0_all, t1_all = events[0][0], events[-1][0]
        chunks.append(
            {
                "idx": idx,
                "rel": m["rel"],
                "folder": m["folder"],
                "mins": (t1_all - t0_all) / 60.0,
                "trades": m["trades"],
                "t0": t0_all,
                "t1": t1_all,
                "instruments": sorted(books),
                "events": events,
            }
        )
    tmp_path = f"{CACHE_PATH}.{os.getpid()}"
    with open(tmp_path, "wb") as f:
        pickle.dump(chunks, f, protocol=pickle.HIGHEST_PROTOCOL)
    os.replace(tmp_path, CACHE_PATH)
    return chunks


_WORKER_CHUNKS: List[Dict[str, Any]] = []


def _init_worker(cache_path: str):
    global _WORKER_CHUNKS
    with open(cache_path, "rb") as f:
        _WORKER_CHUNKS = pickle.load(f)


def _eval_one_chunk(args: Tuple[int, str]) -> Dict[str, Any]:
    chunk_idx, strategy = args
    ch = _WORKER_CHUNKS[chunk_idx]
    events = ch["events"]
    t0_all, t1_all = ch["t0"], ch["t1"]

    clock = bt.SimClock(t0_all)
    ex = bt.SimExchange(ch["instruments"], clock)
    strat = bt.make_strategy(strategy, ex, clock, "/tmp")
    curve = []
    k = 0
    next_step = t0_all
    stopped = None
    pending, seen = [], 0
    n_ev = len(events)
    try:
        while k < n_ev or clock.t < t1_all:
            t_ev = events[k][0] if k < n_ev else float("inf")
            if t_ev <= next_step:
                if t_ev > clock.t:
                    clock.t = t_ev
                _, kind, inst, x, y = events[k]
                if kind == 0:
                    ex.on_book(inst, x, y)
                else:
                    ex.on_trade(inst, x, y)
                k += 1
                continue
            if next_step > t1_all:
                break
            if next_step > clock.t:
                clock.t = next_step
            strat.step()
            curve.append(
                (
                    clock.t - t0_all,
                    ex.get_pnl(valuations=bt.mids(ex)),
                    sum(abs(v) for v in ex.pos.values()),
                )
            )
            next_step = max(next_step + bt.STEP, clock.t)
            while seen < len(ex.fills):
                ex.fills[seen].marks = {}
                pending.append(ex.fills[seen])
                seen += 1
            if pending:
                still = []
                for f in pending:
                    for h in bt.MARKOUTS:
                        if h not in f.marks and clock.t - f.timestamp >= h:
                            m = ex.mid(f.instrument_id)
                            if m is not None:
                                s = 1 if f.side == "bid" else -1
                                f.marks[h] = s * (m - f.price)
                    if len(f.marks) < len(bt.MARKOUTS):
                        still.append(f)
                pending = still
    except bt.Disconnected as exc:
        stopped = str(exc)

    final_pnl = curve[-1][1] if curve else 0.0
    peak, dd = 0.0, 0.0
    for _, p, _ in curve:
        if p > peak:
            peak = p
        if p - peak < dd:
            dd = p - peak
    half_t = (t1_all - t0_all) / 2.0
    first_half = [p for t, p, _ in curve if t <= half_t]
    h1 = first_half[-1] if first_half else 0.0
    h2 = final_pnl - h1
    max_gross = max((g for _, _, g in curve), default=0)

    m = bt.mids(ex)
    per_stock = {}
    mk1_sum = mk1_n = mk5_sum = mk5_n = 0.0
    for i in ex.instruments:
        per_stock[i] = ex.cash[i] + ex.pos[i] * m.get(i, 0.0)
    for f in ex.fills:
        marks = getattr(f, "marks", {})
        if 1 in marks:
            mk1_sum += marks[1]
            mk1_n += 1
        if 5 in marks:
            mk5_sum += marks[5]
            mk5_n += 1

    return {
        "idx": chunk_idx,
        "rel": ch["rel"],
        "mins": ch["mins"],
        "pnl": final_pnl,
        "h1": h1,
        "h2": h2,
        "dd": dd,
        "stopped": stopped,
        "max_req_sec": ex.max_req_sec,
        "max_gross": max_gross,
        "pass_fills": sum(f.kind == "passiv" for f in ex.fills),
        "aggr_fills": sum(f.kind == "aggressiv" for f in ex.fills),
        "per_stock": per_stock,
        "mk1_sum": mk1_sum,
        "mk1_n": int(mk1_n),
        "mk5_sum": mk5_sum,
        "mk5_n": int(mk5_n),
    }


def evaluate_strategies(
    strategies: List[str],
    workers: int = 8,
    chunk_indices: List[int] | None = None,
) -> List[Dict[str, Any]]:
    global _WORKER_CHUNKS
    chunks = build_or_load_cache(force=False)
    _WORKER_CHUNKS = chunks
    n_total = len(chunks)
    idxs = chunk_indices if chunk_indices is not None else list(range(n_total))
    tasks = [(ci, s) for s in strategies for ci in idxs]

    raw_by_strat: Dict[str, List[Dict[str, Any]]] = {s: [] for s in strategies}
    use_fork = "fork" in mp.get_all_start_methods()
    pool_kwargs: Dict[str, Any] = {"max_workers": workers}
    if use_fork:
        pool_kwargs["mp_context"] = mp.get_context("fork")
    else:
        pool_kwargs["initializer"] = _init_worker
        pool_kwargs["initargs"] = (CACHE_PATH,)

    with ProcessPoolExecutor(**pool_kwargs) as pool:
        for res, (_, s) in zip(pool.map(_eval_one_chunk, tasks, chunksize=4), tasks):
            raw_by_strat[s].append(res)

    summaries = []
    for s in strategies:
        rows = sorted(raw_by_strat[s], key=lambda r: r["idx"])
        n_chunks = len(rows)
        total_pnl = sum(r["pnl"] for r in rows)
        train_h1 = sum(r["pnl"] for r in rows if r["idx"] < 12)
        oos_h2 = sum(r["pnl"] for r in rows if r["idx"] >= 12)
        era1 = sum(r["pnl"] for r in rows if r["idx"] < 8)
        era2 = sum(r["pnl"] for r in rows if 8 <= r["idx"] < 16)
        era3 = sum(r["pnl"] for r in rows if r["idx"] >= 16)
        intra_h1 = sum(r["h1"] for r in rows)
        intra_h2 = sum(r["h2"] for r in rows)
        pos_chunks = sum(1 for r in rows if r["pnl"] > 0 and r["stopped"] is None)
        disconnects = sum(1 for r in rows if r["stopped"] is not None)
        worst_chunk = min(r["pnl"] for r in rows)
        best_chunk = max(r["pnl"] for r in rows)
        worst_dd = min(r["dd"] for r in rows)
        max_req = max(r["max_req_sec"] for r in rows)
        p_fills = sum(r["pass_fills"] for r in rows)
        a_fills = sum(r["aggr_fills"] for r in rows)
        mk1_n = sum(r["mk1_n"] for r in rows)
        mk5_n = sum(r["mk5_n"] for r in rows)
        mk1 = sum(r["mk1_sum"] for r in rows) / mk1_n if mk1_n else 0.0
        mk5 = sum(r["mk5_sum"] for r in rows) / mk5_n if mk5_n else 0.0

        stock_pnl = {k: round(sum(r["per_stock"].get(k, 0.0) for r in rows), 1) for k in ALL_STOCKS}
        summaries.append(
            {
                "strategy": s,
                "total_pnl": round(total_pnl, 1),
                "train_12": round(train_h1, 1),
                "oos_12": round(oos_h2, 1),
                "oos_ratio": round(oos_h2 / max(1.0, train_h1), 3) if train_h1 > 0 else 0.0,
                "era_3": [round(era1, 1), round(era2, 1), round(era3, 1)],
                "intra_halves": [round(intra_h1, 1), round(intra_h2, 1)],
                "pos_chunks": pos_chunks,
                "n_chunks": n_chunks,
                "disconnects": disconnects,
                "worst_chunk": round(worst_chunk, 1),
                "best_chunk": round(best_chunk, 1),
                "worst_dd": round(worst_dd, 1),
                "calmar": round(total_pnl / max(1.0, abs(worst_dd)), 1),
                "max_req_sec": max_req,
                "pass_fills": p_fills,
                "aggr_fills": a_fills,
                "mk1": round(mk1, 4),
                "mk5": round(mk5, 4),
                "stock_pnl": stock_pnl,
                "chunks": rows,
            }
        )
    return summaries


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("strategies", nargs="*", help="List of module:Class strategy specs")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 4) - 1))
    ap.add_argument("--rebuild-cache", action="store_true")
    ap.add_argument("--list-chunks", action="store_true")
    ap.add_argument("--json-out", type=str, default="")
    ap.add_argument("--per-chunk", action="store_true")
    ap.add_argument("--chunk-slice", type=str, default="", help="e.g. 0:12 for Train only, 12:24 for OOS only")
    args = ap.parse_args()

    t0 = time.perf_counter()
    chunks = build_or_load_cache(force=args.rebuild_cache)
    if args.list_chunks:
        tot_m = sum(c["mins"] for c in chunks)
        tot_t = sum(c["trades"] for c in chunks)
        for c in chunks:
            print(f"{c['idx']:2d} | {c['mins']:6.2f}m | {c['trades']:6d} tr | {c['rel']}")
        print(f"Selected {len(chunks)} chunks | {tot_m:.2f} min | {tot_t} trades (cache ready in {time.perf_counter()-t0:.2f}s)")
        if not args.strategies:
            return

    if not args.strategies:
        ap.error("Provide at least one strategy or --list-chunks")

    chunk_indices = None
    if args.chunk_slice:
        a_s, b_s = args.chunk_slice.split(":")
        chunk_indices = list(range(int(a_s or 0), int(b_s or len(chunks))))

    results = evaluate_strategies(args.strategies, workers=args.workers, chunk_indices=chunk_indices)
    sel_chunks = [chunks[i] for i in (chunk_indices or range(len(chunks)))]
    dt = time.perf_counter() - t0
    print(
        f"\n=== {len(sel_chunks)}-CHUNK BENCHMARK ({sum(c['mins'] for c in sel_chunks):.1f} min, "
        f"{sum(c['trades'] for c in sel_chunks):,} trades, elapsed {dt:.2f}s) ==="
    )
    hdr = (
        f"{'Strategy':<36} | {'Total PnL':>10} | {'Train12':>9} | {'OOS12':>9} | "
        f"{'Win':>5} | {'Disc':>4} | {'WorstCh':>8} | {'MaxDD':>8} | {'Req':>3} | {'Fills (P/A)':>15}"
    )
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        fills_s = f"{r['pass_fills']}/{r['aggr_fills']}"
        print(
            f"{r['strategy']:<36} | {r['total_pnl']:>+10.1f} | {r['train_12']:>+9.1f} | {r['oos_12']:>+9.1f} | "
            f"{r['pos_chunks']:>2d}/{r['n_chunks']:<2d} | {r['disconnects']:>4d} | {r['worst_chunk']:>+8.1f} | "
            f"{r['worst_dd']:>8.1f} | {r['max_req_sec']:>3d} | {fills_s:>15}"
        )
        sp_str = "  Stocks: " + ", ".join(f"{k}:{v:+.0f}" for k, v in r["stock_pnl"].items())
        era_str = f" | Eras(8x3): {r['era_3']} | IntraH1/H2: {r['intra_halves']} | mk1/5: {r['mk1']:+.3f}/{r['mk5']:+.3f}"
        print(sp_str + era_str)
        if args.per_chunk:
            for cr in r["chunks"]:
                st = f" DISC:{cr['stopped']}" if cr["stopped"] else ""
                print(
                    f"    [{cr['idx']:02d}] {cr['rel']:<46} {cr['mins']:5.1f}m | "
                    f"PnL {cr['pnl']:+8.1f} (H1 {cr['h1']:+7.1f} / H2 {cr['h2']:+7.1f}) | DD {cr['dd']:7.1f}{st}"
                )

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2)


if __name__ == "__main__":
    main()
