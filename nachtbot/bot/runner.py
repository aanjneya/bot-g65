"""Live trading runner for the Optibook ABMM-S (Adaptive Bayesian Multi-Asset MM & Sniper) bot."""

from __future__ import annotations

import argparse
import csv
import logging
import os
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from bot.config import BotConfig
from bot.execution import RiskAndExecution
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
            for name in ("books.csv", "trades.csv", "news.csv")
        ]
        self._books_w, self._trades_w, self._news_w = (csv.writer(f) for f in self._files)
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

    def __init__(self, exchange: Any, cfg: BotConfig) -> None:
        self.exchange = exchange
        self.cfg = cfg
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

        # 1. Single-connection market data ingest (books, public TradeTicks, social feeds)
        books: Dict[str, Any] = {}
        trade_ticks: Dict[str, List[Any]] = {}
        for inst_id in self.instruments:
            books[inst_id] = self.exchange.get_last_price_book(inst_id)
            if hasattr(self.exchange, "poll_new_trade_ticks"):
                try:
                    trade_ticks[inst_id] = self.exchange.poll_new_trade_ticks(inst_id) or []
                except Exception:
                    trade_ticks[inst_id] = []
            else:
                trade_ticks[inst_id] = []

        try:
            social_feeds = self.exchange.poll_new_social_media_feeds() or []
        except Exception:
            social_feeds = []

        if self.market_logger is not None:
            try:
                self.market_logger.record(books, trade_ticks, social_feeds)
            except Exception as exc:
                logger.warning("CSV logger error: %s", exc)

        positions = self.exchange.get_positions()

        # 2. Compute ABMM-S snapshot (Microprice+OFI, V>250 wall, counterparty markout, lead-lag, GLFT, news, endgame)
        alpha = self._ensure_alpha()
        snap = alpha.update(
            now=now_ts,
            tick_count=self.tick_count,
            books=books,
            instruments=self.instruments,
            positions=positions,
            trade_ticks=trade_ticks,
            social_feeds=social_feeds,
        )

        orders_sent = 0

        # 3. Priority 1 — Endgame flattener (final 45s of 30-min session)
        if snap.endgame.should_flatten:
            orders_sent += self.executor.flatten_positions(books, self.instruments)
        else:
            # 4. Priority 2 — <1ms News sentiment sniper (bounded IOC, cancels resting quotes first)
            news_sniped = self.executor.execute_news_snipes(snap.news_snipes, books, self.instruments)
            orders_sent += len(news_sniped)

            # 5. Priority 3 — Edge-only aggressive IOC (only when book is mispriced >= 3 ticks & 1.5*spread beyond FV)
            edge_sniped = self.executor.execute_fair_value_edge(
                snap.fair_values,
                books,
                self.instruments,
                paused_instruments=snap.paused | news_sniped,
                max_edge_lots=self.cfg.edge_ioc_lots,
                min_edge_ticks=self.cfg.edge_ioc_min_ticks,
            )
            orders_sent += len(edge_sniped)

            # 6. Priority 4 — Queue-preserving passive GLFT quoting around reservation price
            if self.cfg.enable_passive_quoting and snap.quote_plans and self.exchange.is_connected():
                orders_sent += self.executor.quote_from_plans(
                    snap.quote_plans,
                    books,
                    self.instruments,
                    paused_instruments=snap.paused | news_sniped | edge_sniped,
                )

        if self.tick_count % self.cfg.log_every_ticks == 0 and self.exchange.is_connected():
            pnl = self.exchange.get_pnl()
            pos = {k: v for k, v in self.exchange.get_positions().items() if v != 0}
            rem_str = f" rem={snap.endgame.remaining_sec:.0f}s" if snap.endgame.remaining_sec is not None else ""
            logger.info(
                "tick=%d paused=%d gamma_x=%.1f%s pnl=%.2f positions=%s",
                self.tick_count,
                len(snap.paused),
                snap.endgame.gamma_mult,
                rem_str,
                pnl if pnl is not None else 0.0,
                pos,
            )

        return orders_sent

    def run(self, max_ticks: Optional[int] = None) -> None:
        self.exchange.connect()
        try:
            all_insts = self.exchange.get_tradable_instruments()
            self.instruments = {k: v for k, v in all_insts.items() if k in self.cfg.universe}
            if not self.instruments:
                self.instruments = all_insts

            self._ensure_alpha()
            if self.cfg.enable_csv_logging:
                log_dir = self.cfg.csv_log_dir or ("data_live_" + datetime.now().strftime("%d_%H%M%S"))
                self.market_logger = MarketLogger(log_dir)
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
                self.market_logger.close()
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
    args = parser.parse_args()

    cfg = BotConfig()
    if args.mock:
        cfg.mock_mode = True
    if args.session_sec is not None:
        cfg.session_duration_sec = args.session_sec
    if args.log_csv or args.log_dir:
        cfg.enable_csv_logging = True
    if args.log_dir:
        cfg.csv_log_dir = args.log_dir

    exchange = build_exchange(cfg)
    bot = ABMMSBot(exchange, cfg)
    bot.run(max_ticks=args.max_ticks)


if __name__ == "__main__":
    main()
