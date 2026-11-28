"""Live trading runner for the Optibook ABMM-S (Adaptive Bayesian Multi-Asset MM & Sniper) bot."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

# Ensure project root is on sys.path when invoked as `python bot/runner.py` or via `bot/backtest.py`
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from bot.config import BotConfig
from bot.cycle_budget import CycleBudget
from bot.execution import RiskAndExecution, mid_price, strip_own_orders
from bot.lag_taker import LagTaker
from bot.mock_exchange import MockExchange
from bot.signals import ABMMSAlphaEngine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("abmms-optibook")


def build_exchange(cfg: BotConfig) -> Any:
    """Create either MockExchange (MOCK_MODE=true) or optibook Exchange."""
    if cfg.mock_mode:
        logger.info("Starting in MOCK_MODE=true with synthetic correlated AI market")
        return MockExchange(universe=cfg.universe, max_pos=cfg.max_position_lots)

    from optibook.synchronous_client import Exchange  # type: ignore

    kwargs: Dict[str, Any] = {}
    if cfg.exchange_host:
        kwargs["host"] = cfg.exchange_host
    if cfg.info_port is not None:
        kwargs["info_port"] = cfg.info_port
    if cfg.exec_port is not None:
        kwargs["exec_port"] = cfg.exec_port
    if cfg.username:
        kwargs["username"] = cfg.username
    if cfg.password:
        kwargs["password"] = cfg.password

    logger.info("Connecting to Optibook Exchange (%s)", cfg.exchange_host or "env/default config")
    return Exchange(**kwargs)


class MarketLogger:
    """
    Lightweight CSV recorder embedded inside the live bot (compatible with collector.py / muster.py).
    Avoids opening a second Optibook connection (which would silently kick the live bot).
    """

    def __init__(self, folder: str, flush_every_sec: float = 15.0) -> None:
        self.folder = folder
        self.flush_every_sec = flush_every_sec
        os.makedirs(folder, exist_ok=True)
        self._files = [
            open(os.path.join(folder, name), "w", newline="", encoding="utf-8")
            for name in ("books.csv", "trades.csv", "news.csv", "telemetry.jsonl")
        ]
        self._books_w, self._trades_w, self._news_w = (csv.writer(f) for f in self._files[:3])
        self._telemetry_f = self._files[3]
        self._books_w.writerow(["time", "instrument", "bids", "asks"])
        self._trades_w.writerow(["time", "instrument", "price", "volume", "aggressor_side", "buyer", "seller"])
        self._news_w.writerow(["time", "post"])
        self._last_book: Dict[str, Tuple[str, str]] = {}
        self._last_flush = time.monotonic()

    @staticmethod
    def _fmt_levels(side: Any) -> str:
        return "|".join(f"{float(lv.price):.2f}:{int(lv.volume)}" for lv in (side or []))

    def record(
        self,
        books: Dict[str, Any],
        trade_ticks: Dict[str, List[Any]],
        social_feeds: List[Any],
    ) -> None:
        now_iso = datetime.now().isoformat()
        for inst_id, book in books.items():
            if book is None:
                continue
            row = (
                self._fmt_levels(getattr(book, "bids", None)),
                self._fmt_levels(getattr(book, "asks", None)),
            )
            if row != self._last_book.get(inst_id):
                self._books_w.writerow([now_iso, inst_id, row[0], row[1]])
                self._last_book[inst_id] = row

        for inst_id, ticks in trade_ticks.items():
            for t in ticks:
                self._trades_w.writerow(
                    [
                        now_iso,
                        inst_id,
                        getattr(t, "price", ""),
                        getattr(t, "volume", ""),
                        getattr(t, "aggressor_side", ""),
                        getattr(t, "buyer", ""),
                        getattr(t, "seller", ""),
                    ]
                )

        for feed in social_feeds:
            self._news_w.writerow([now_iso, getattr(feed, "post", "")])

        now_mono = time.monotonic()
        if now_mono - self._last_flush >= self.flush_every_sec:
            self.flush()
            self._last_flush = now_mono

    def record_telemetry(self, payload: Dict[str, Any]) -> None:
        self._telemetry_f.write(json.dumps(payload, separators=(",", ":")) + "\n")
        now_mono = time.monotonic()
        if now_mono - self._last_flush >= self.flush_every_sec:
            self.flush()
            self._last_flush = now_mono

    def flush(self) -> None:
        for f in self._files:
            try:
                f.flush()
            except Exception:
                pass

    def close(self) -> None:
        self.flush()
        for f in self._files:
            try:
                f.close()
            except Exception:
                pass


class ABMMSBot:
    """Live/Mock ABMM-S trading bot: multi-signal FV, 8x8 GLFT skew, news sniper, endgame glide."""

    def __init__(
        self,
        exchange: Any,
        cfg: BotConfig,
        on_chunk_complete: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.exchange = exchange
        self.cfg = cfg
        self.cycle_budget = CycleBudget()
        self.lag_taker = LagTaker(cfg.edge_ioc_lots)
        self.lag_due: Dict[str, float] = {}
        self.executor = RiskAndExecution(
            exchange,
            max_position_lots=cfg.max_position_lots,
            max_open_order_volume=cfg.max_open_order_volume,
            max_requests_per_sec=cfg.max_requests_per_sec,
        )
        self.instruments: Dict[str, Any] = {}
        self.alpha: Optional[ABMMSAlphaEngine] = None
        self.market_logger: Optional[MarketLogger] = None
        self.tick_count = 0
        self._peak_pnl: Optional[float] = None
        self._drawdown_halted: bool = False
        self._chunk_started_at: Optional[float] = None
        self._nacht_dir: Optional[str] = None
        self._nacht_procs: List[subprocess.Popen[Any]] = []
        self._on_chunk_complete = on_chunk_complete

    def _new_log_dir(self) -> str:
        return os.path.join("data", "live_" + datetime.now().strftime("%d_%H%M%S_%f")[:16])

    def _trigger_nacht_analysis(self, finished_folder: str) -> None:
        if self._on_chunk_complete is not None:
            self._on_chunk_complete(finished_folder)
            return
        nacht_dir = self._nacht_dir or self.cfg.nacht_dir or os.path.join(
            "data", "nacht_" + datetime.now().strftime("%d_%H%M")
        )
        self._nacht_dir = nacht_dir
        os.makedirs(nacht_dir, exist_ok=True)
        nacht_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "nacht.py")
        cmd = [
            sys.executable,
            nacht_script,
            "--nur-auswerten",
            os.path.abspath(finished_folder),
            "--ordner",
            os.path.abspath(nacht_dir),
            "--strategien",
            self.cfg.nacht_strategies,
        ]
        log_path = os.path.join(nacht_dir, "nacht_bg.log")
        try:
            with open(log_path, "a", encoding="utf-8") as bg_log:
                proc = subprocess.Popen(cmd, stdout=bg_log, stderr=subprocess.STDOUT)
            self._nacht_procs = [p for p in self._nacht_procs if p.poll() is None] + [proc]
            logger.info("Background nacht analysis started for %s -> %s/nacht_bericht.md", finished_folder, nacht_dir)
        except Exception as exc:
            logger.warning("Failed to start background nacht analysis: %s", exc)

    def _maybe_rotate_chunk(self, now_ts: float) -> None:
        if self.market_logger is None or not self.cfg.nacht_mode or self.cfg.nacht_chunk_min <= 0:
            return
        if self._chunk_started_at is None:
            self._chunk_started_at = now_ts
            return
        if now_ts - self._chunk_started_at < self.cfg.nacht_chunk_min * 60.0:
            return
        finished = self.market_logger.folder
        self.market_logger.close()
        new_dir = self._new_log_dir()
        if new_dir == finished:
            new_dir = f"{new_dir}_{self.tick_count}"
        self.market_logger = MarketLogger(new_dir)
        self._chunk_started_at = now_ts
        logger.info("Rotated CSV chunk %s -> %s", finished, new_dir)
        self._trigger_nacht_analysis(finished)

    def _ensure_alpha(self) -> ABMMSAlphaEngine:
        if self.alpha is None:
            universe = sorted(self.instruments.keys()) if self.instruments else list(self.cfg.universe)
            self.alpha = ABMMSAlphaEngine(
                universe,
                w_micro=self.cfg.w_micro,
                w_ofi=self.cfg.w_ofi,
                w_wall=self.cfg.w_wall,
                wall_min_volume=self.cfg.wall_min_volume,
                w_leadlag=self.cfg.w_leadlag,
                glft_gamma=self.cfg.glft_gamma,
                base_edge_ticks=float(self.cfg.quote_edge_ticks),
                vol_spread_mult=self.cfg.vol_spread_mult,
                quote_lots=self.cfg.quote_lots,
                max_position_lots=self.cfg.max_position_lots,
                max_net_portfolio_lots=self.cfg.max_net_portfolio_lots,
                tail_stop_ticks=self.cfg.tail_stop_ticks,
                news_cooldown_ticks=self.cfg.news_cooldown_ticks,
                enable_news_sniper=self.cfg.enable_news_sniper,
                news_snipe_lots=self.cfg.news_snipe_lots,
                session_duration_sec=self.cfg.session_duration_sec,
                endgame_start_sec=self.cfg.endgame_start_sec,
                flatten_start_sec=self.cfg.flatten_start_sec,
                endgame_gamma_mult=self.cfg.endgame_gamma_mult,
                own_username=self.cfg.username,
            )
        return self.alpha

    @property
    def news_cooldown_until(self) -> Dict[str, int]:
        return self._ensure_alpha().news_cooldown_until

    def step(self, now: Optional[float] = None) -> int:
        """Run one market-data + ABMM-S signal + risk-guarded execution cycle. Returns orders sent."""
        if not self.exchange.is_connected():
            self.exchange.connect()

        if not self.instruments:
            all_insts = self.exchange.get_tradable_instruments()
            self.instruments = {k: v for k, v in all_insts.items() if k in self.cfg.universe} or all_insts

        if isinstance(self.exchange, MockExchange):
            self.exchange.step_market()

        self.tick_count += 1
        now_ts = time.monotonic() if now is None else float(now)

        # 1. Single-connection market data ingest — strip our own resting orders from books FIRST!
        open_orders_by_inst: Dict[str, Dict[int, Any]] = {}
        books: Dict[str, Any] = {}
        trade_ticks: Dict[str, List[Any]] = {}
        own_trades: Dict[str, List[Any]] = {}
        for inst_id, inst in self.instruments.items():
            oo = self.exchange.get_outstanding_orders(inst_id)
            open_orders_by_inst[inst_id] = oo
            raw_book = self.exchange.get_last_price_book(inst_id)
            tick = float(getattr(inst, "tick_size", 0.10) or 0.10)
            books[inst_id] = strip_own_orders(raw_book, oo, tick_size=tick)

            if hasattr(self.exchange, "poll_new_trade_ticks"):
                try:
                    trade_ticks[inst_id] = self.exchange.poll_new_trade_ticks(inst_id) or []
                except Exception:
                    trade_ticks[inst_id] = []
            else:
                trade_ticks[inst_id] = []

            if hasattr(self.exchange, "poll_new_trades"):
                try:
                    own_trades[inst_id] = self.exchange.poll_new_trades(inst_id) or []
                except Exception:
                    own_trades[inst_id] = []
            else:
                own_trades[inst_id] = []

        try:
            social_feeds = self.exchange.poll_new_social_media_feeds() or []
        except Exception:
            social_feeds = []

        if self.market_logger is not None:
            try:
                self.market_logger.record(books, trade_ticks, social_feeds)
                self._maybe_rotate_chunk(now_ts)
            except Exception as exc:
                logger.warning("CSV logger error: %s", exc)

        self.executor.passive_evidence.observe(
            now_ts, trade_ticks, {u: mid_price(b) for u, b in books.items()}, self.cfg.username
        )
        self.executor.roundtrip_screen.observe(
            now_ts, books, self.instruments, trade_ticks, self.cfg.tail_stop_ticks
        )
        positions = self.exchange.get_positions()

        # 2. Compute ABMM-S snapshot (wall-protected entry, profit exit, scratches, markout breaker, GLFT)
        alpha = self._ensure_alpha()
        snap = alpha.update(
            now=now_ts,
            tick_count=self.tick_count,
            books=books,
            instruments=self.instruments,
            positions=positions,
            trade_ticks=trade_ticks,
            social_feeds=social_feeds,
            own_trades=own_trades,
        )

        self.cycle_budget.observe(now_ts, self.instruments, positions, own_trades, trade_ticks, self.cfg.username)
        self.cycle_budget.apply(now_ts, snap, self.instruments, books, positions)

        lag_ready = self.lag_taker.observe(now_ts, books, self.cfg.tail_stop_ticks) if self.cfg.enable_edge_ioc else []
        for u, due in list(self.lag_due.items()):
            if not int(positions.get(u, 0)):
                del self.lag_due[u]
                continue
            # Isolate the measured position. Risk exits always retain priority.
            snap.paused.add(u)
            snap.news_snipes.pop(u, None)
            if now_ts >= due:
                snap.scratches.setdefault(u, 'lag_horizon')

        # 2b. Portfolio max-drawdown circuit breaker (normal 2-day continuous safety rail)
        if self.cfg.max_session_drawdown > 0 and self.exchange.is_connected():
            cur_pnl = self.exchange.get_pnl()
            if cur_pnl is not None:
                self._peak_pnl = cur_pnl if self._peak_pnl is None else max(self._peak_pnl, cur_pnl)
                if self._peak_pnl - cur_pnl >= self.cfg.max_session_drawdown:
                    if not self._drawdown_halted:
                        logger.warning(
                            "DRAWDOWN BREAKER triggered (peak=%.2f cur=%.2f dd=%.2f >= %.2f) — flattening",
                            self._peak_pnl,
                            cur_pnl,
                            self._peak_pnl - cur_pnl,
                            self.cfg.max_session_drawdown,
                        )
                        self._drawdown_halted = True

        orders_sent = 0

        # 3. Priority 1 — Drawdown halt or optional endgame flattener
        if self._drawdown_halted or snap.endgame.should_flatten:
            orders_sent += self.executor.flatten_positions(books, self.instruments)
        else:
            # 4. Priority 2 — Extreme tail stop-loss IOC scratches (only >= 10 ticks adverse)
            scratched = self.executor.execute_scratches(snap.scratches, books, self.instruments)
            orders_sent += len(scratched)

            # 5. Priority 3 — <1ms News sentiment sniper (bounded IOC, cancels resting quotes first)
            news_sniped = self.executor.execute_news_snipes(
                {k: v for k, v in snap.news_snipes.items() if k not in scratched},
                books,
                self.instruments,
            )
            orders_sent += len(news_sniped)

            # 6. Take only a prospectively measured, still executable follower quote.
            edge_sniped: set[str] = set()
            if lag_ready and not news_sniped and snap.endgame.inv_inc_scale >= 1.0:
                current = self.exchange.get_positions()
                net = sum(int(current.get(u, 0)) for u in self.instruments)
                room = {1: self.cfg.max_net_portfolio_lots - net, -1: self.cfg.max_net_portfolio_lots + net}
                for _, u, sign, entry in lag_ready:
                    plan = snap.quote_plans.get(u)
                    if plan is None or u not in snap.fair_values or u in snap.paused or u in snap.scratches:
                        continue
                    if u in scratched or current.get(u, 0) or u in self.lag_due:
                        continue
                    approved = plan.bid_lots if sign == 1 else plan.ask_lots
                    lots = self.lag_taker.lots
                    if min(approved, room[sign]) < lots:
                        continue
                    if self.executor.open_lag_ioc(u, self.instruments[u], sign, entry, lots):
                        room[sign] -= lots
                        self.lag_due[u] = now_ts + self.lag_taker.horizon
                        edge_sniped.add(u)
                orders_sent += len(edge_sniped)

            # Exclude possible aggressive additions from the passive-entry ledger. A scratch that
            # only closes inventory still belongs to the entry cycle and must count at its fill price.
            self.cycle_budget.flag_opening_iocs(now_ts, news_sniped | edge_sniped)

            # 7. Priority 5 — Pure ABMM-S passive GLFT quoting around reservation price r_t = F_hat - gamma*Sigma*q
            if self.cfg.enable_passive_quoting and snap.quote_plans and self.exchange.is_connected():
                orders_sent += self.executor.quote_from_plans(
                    snap.quote_plans,
                    books,
                    self.instruments,
                    paused_instruments=snap.paused | scratched | news_sniped | edge_sniped,
                    open_orders_by_inst=open_orders_by_inst,
                    wall_min_volume=self.cfg.wall_min_volume,
                )

        if self.tick_count % self.cfg.log_every_ticks == 0 and self.exchange.is_connected():
            pnl = self.exchange.get_pnl()
            pos = {k: v for k, v in self.exchange.get_positions().items() if v != 0}
            mk = {k: f"{v[1]:+.2f}t({v[0]})" for k, v in alpha.own_markout_stats.items() if v[0] > 0}
            rem_str = f" rem={snap.endgame.remaining_sec:.0f}s" if snap.endgame.remaining_sec is not None else ""
            logger.info(
                "tick=%d paused=%d gamma_x=%.1f%s pnl=%.2f pos=%s mk5s=%s",
                self.tick_count,
                len(snap.paused),
                snap.endgame.gamma_mult,
                rem_str,
                pnl if pnl is not None else 0.0,
                pos,
                mk,
            )
            if self.market_logger is not None:
                try:
                    self.market_logger.record_telemetry(
                        {
                            "t": datetime.now().isoformat(),
                            "tick": self.tick_count,
                            "pnl": round(float(pnl), 2) if pnl is not None else 0.0,
                            "pos": pos,
                            "mk5s": {
                                k: {"n": v[0], "avg_ticks": round(float(v[1]), 3)}
                                for k, v in alpha.own_markout_stats.items()
                                if v[0] > 0
                            },
                            "paused": sorted(snap.paused),
                            "orders_sent": orders_sent,
                            "drawdown_halted": self._drawdown_halted,
                        }
                    )
                except Exception as exc:
                    logger.warning("Telemetry logger error: %s", exc)

        return orders_sent

    def run(self, max_ticks: Optional[int] = None) -> None:
        self.exchange.connect()
        try:
            all_insts = self.exchange.get_tradable_instruments()
            self.instruments = {k: v for k, v in all_insts.items() if k in self.cfg.universe}
            if not self.instruments:
                self.instruments = all_insts

            self._ensure_alpha()
            if self.cfg.nacht_mode:
                self.cfg.enable_csv_logging = True
                if not self._nacht_dir:
                    self._nacht_dir = self.cfg.nacht_dir or os.path.join(
                        "data", "nacht_" + datetime.now().strftime("%d_%H%M")
                    )
            if self.cfg.enable_csv_logging and self.market_logger is None:
                log_dir = self.cfg.csv_log_dir or self._new_log_dir()
                self.market_logger = MarketLogger(log_dir)
                self._chunk_started_at = time.monotonic()
                logger.info("Embedded CSV logger active -> %s", log_dir)

            logger.info(
                "ABMM-S live on %s (mock=%s, session=%ss)",
                sorted(self.instruments.keys()),
                self.cfg.mock_mode,
                int(self.cfg.session_duration_sec),
            )

            while max_ticks is None or self.tick_count < max_ticks:
                self.step()
                if not self.cfg.mock_mode or max_ticks is None:
                    time.sleep(self.cfg.poll_interval_sec)
        except KeyboardInterrupt:
            logger.info("Interrupted — cancelling open orders and disconnecting")
        finally:
            if self.market_logger is not None:
                final_folder = self.market_logger.folder
                self.market_logger.close()
                if self.cfg.nacht_mode:
                    self._trigger_nacht_analysis(final_folder)
            for inst_id in self.instruments:
                try:
                    self.exchange.delete_orders(inst_id)
                except Exception:
                    pass
            try:
                self.exchange.disconnect()
            except Exception:
                pass


# Backward-compatible alias for existing imports/tests
StatArbBot = ABMMSBot


class BacktestBot:
    """
    Pure ABMM-S adapter for bot/backtest.py (normal continuous mode, no endgame timer):
        python bot/backtest.py <data_folder> --strategy runner:BacktestBot
    Uses SimClock automatically when backtest.py patches runner.time = clock.
    """

    def __init__(self, ex: Any, folder: str) -> None:
        cfg = BotConfig(
            mock_mode=False,
            session_duration_sec=0.0,
            quote_lots=5,
            max_position_lots=40,
            max_net_portfolio_lots=120,
            max_requests_per_sec=18,
            log_every_ticks=10_000,
        )
        # Share backtest.py's SimClock with execution.py's rate-limiter
        import bot.execution as _exec_mod

        _exec_mod.time = time  # type: ignore[assignment]
        self._bot = ABMMSBot(ex, cfg)
        self._bot.instruments = ex.get_tradable_instruments()

    def step(self) -> None:
        self._bot.step(now=time.time())


def main() -> None:
    parser = argparse.ArgumentParser(description="Optibook ABMM-S Trading Bot")
    parser.add_argument("--mock", action="store_true", help="Run against local MockExchange")
    parser.add_argument("--max-ticks", type=int, default=None, help="Stop after N ticks (useful for testing)")
    parser.add_argument(
        "--session-sec",
        type=float,
        default=None,
        help="Competition session duration in seconds (e.g. 1800 for 30-min Sunday final)",
    )
    parser.add_argument("--log-csv", action="store_true", help="Enable embedded CSV market logger")
    parser.add_argument("--log-dir", type=str, default=None, help="Directory for embedded CSV logs")
    parser.add_argument(
        "--nacht",
        action="store_true",
        help="Trade + record hourly CSV chunks + run background nacht.py backtest/muster analysis",
    )
    parser.add_argument("--chunk-min", type=float, default=None, help="Minutes per CSV chunk in --nacht mode (default 60)")
    parser.add_argument("--nacht-strategies", type=str, default=None, help="Comma-separated strategies for --nacht analysis")
    args = parser.parse_args()

    cfg = BotConfig()
    if args.mock:
        cfg.mock_mode = True
    if args.session_sec is not None:
        cfg.session_duration_sec = args.session_sec
    if args.log_csv or args.log_dir or args.nacht:
        cfg.enable_csv_logging = True
    if args.log_dir:
        cfg.csv_log_dir = args.log_dir
    if args.nacht:
        cfg.nacht_mode = True
    if args.chunk_min is not None:
        cfg.nacht_chunk_min = args.chunk_min
    if args.nacht_strategies:
        cfg.nacht_strategies = args.nacht_strategies

    exchange = build_exchange(cfg)
    bot = ABMMSBot(exchange, cfg)
    bot.run(max_ticks=args.max_ticks)


if __name__ == "__main__":
    main()
