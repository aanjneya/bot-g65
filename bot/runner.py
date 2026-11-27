"""Live trading runner for the Optibook Kalman StatArb bot."""

from __future__ import annotations

import argparse
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from bot.config import BotConfig
from bot.execution import RiskAndExecution, mid_price
from bot.kalman import BasketFairValueModel, OnlineKalmanPair, select_top_pairs
from bot.mock_exchange import MockExchange

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("statarb-optibook")


def build_exchange(cfg: BotConfig) -> Any:
    """Create either MockExchange (MOCK_MODE=true) or optibook Exchange."""
    if cfg.mock_mode:
        logger.info("Starting in MOCK_MODE=true with synthetic cointegrated market")
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


def seed_history_from_optibook(cfg: BotConfig, instruments: List[str]) -> Dict[str, List[float]]:
    """Optionally seed recent mid-prices from Optibook HistoricalDataLoader."""
    history: Dict[str, List[float]] = {inst: [] for inst in instruments}
    if cfg.mock_mode or not cfg.use_historical_seed:
        return history

    try:
        from optibook.historical_data_loader import HistoricalDataLoader  # type: ignore

        loader = HistoricalDataLoader()
        end = datetime.now(timezone.utc)
        start = end - timedelta(hours=2)
        trades_df = loader.get_trades_df(start, end)
        if trades_df is not None and not trades_df.empty:
            for inst in instruments:
                sub = trades_df[trades_df["instrument_id"] == inst]
                if not sub.empty:
                    history[inst] = [float(p) for p in sub["price"].tail(200).tolist()]
            logger.info("Seeded historical prices for %d instruments", sum(1 for v in history.values() if v))
    except Exception as exc:
        logger.info("Historical seed skipped (%s) — warming up from live ticks", exc)

    return history


class StatArbBot:
    """Manages pair discovery, online Kalman updates, and risk-guarded execution."""

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
        self.history: Dict[str, List[float]] = {}
        self.active_pairs: List[OnlineKalmanPair] = []
        self.basket_model: Optional[BasketFairValueModel] = None
        self.news_cooldown_until: Dict[str, int] = {}
        self.tick_count = 0

    def _build_pair_model(self, stock_a: str, stock_b: str) -> OnlineKalmanPair:
        pair = OnlineKalmanPair(
            stock_a,
            stock_b,
            delta=self.cfg.kalman_delta,
            r_var=self.cfg.kalman_r,
            z_window=self.cfg.z_window,
            min_warmup=self.cfg.min_warmup,
            entry_z1=self.cfg.entry_z1,
            entry_z2=self.cfg.entry_z2,
            exit_z=self.cfg.exit_z,
            stop_z=self.cfg.stop_z,
            t1_weight=self.cfg.t1_weight,
            min_hurdle_bps=self.cfg.min_hurdle_bps,
        )
        ha = self.history.get(stock_a, [])
        hb = self.history.get(stock_b, [])
        n = min(len(ha), len(hb))
        for pa, pb in zip(ha[-n:], hb[-n:]):
            pair.update(pa, pb)
        return pair

    def _refresh_pairs(self) -> None:
        selected = select_top_pairs(
            self.history,
            max_pairs=self.cfg.max_pairs,
            min_points=self.cfg.min_warmup,
        )
        if not selected:
            return

        existing_keys = {(p.stock_a, p.stock_b): p for p in self.active_pairs}
        new_pairs: List[OnlineKalmanPair] = []
        for cand in selected:
            key = (cand.stock_a, cand.stock_b)
            if key in existing_keys:
                new_pairs.append(existing_keys[key])
            else:
                logger.info(
                    "Activated pair %s / %s (hl=%.1f, corr=%.2f, p=%.3f, beta=%.2f)",
                    cand.stock_a,
                    cand.stock_b,
                    cand.half_life,
                    cand.corr,
                    cand.p_value,
                    cand.beta,
                )
                new_pairs.append(self._build_pair_model(cand.stock_a, cand.stock_b))
        self.active_pairs = new_pairs

    def _check_news_guard(self) -> Set[str]:
        """Poll social media feed and pause affected tickers (or all) for cooldown ticks."""
        try:
            feeds = self.exchange.poll_new_social_media_feeds()
        except Exception:
            feeds = []

        for feed in feeds:
            post = str(getattr(feed, "post", "") or "")
            post_upper = post.upper()
            mentioned = [inst for inst in self.instruments if re.search(rf"\b{re.escape(inst)}\b", post_upper)]
            targets = mentioned if mentioned else list(self.instruments.keys())
            for inst in targets:
                self.news_cooldown_until[inst] = self.tick_count + self.cfg.news_cooldown_ticks
            logger.info("News jump guard triggered (%s): pausing %s for %d ticks", post[:60], targets, self.cfg.news_cooldown_ticks)

        return {inst for inst, until in self.news_cooldown_until.items() if self.tick_count < until}

    def step(self) -> int:
        """Run one market-data + signal + execution cycle. Returns orders sent."""
        if not self.exchange.is_connected():
            self.exchange.connect()

        if isinstance(self.exchange, MockExchange):
            self.exchange.step_market()

        self.tick_count += 1
        paused = self._check_news_guard()
        books: Dict[str, Any] = {}
        mids: Dict[str, float] = {}

        for inst_id in self.instruments:
            book = self.exchange.get_last_price_book(inst_id)
            books[inst_id] = book
            mp = mid_price(book)
            hist = self.history.setdefault(inst_id, [])
            if mp is not None:
                mids[inst_id] = mp
                hist.append(mp)
            else:
                hist.append(float("nan"))
            if len(hist) > 500:
                del hist[: len(hist) - 500]

        if not self.active_pairs or self.tick_count % self.cfg.rebalance_pairs_every_ticks == 0:
            self._refresh_pairs()

        fair_values: Dict[str, float] = {}
        if self.cfg.enable_basket_model:
            if self.basket_model is None:
                self.basket_model = BasketFairValueModel(
                    list(self.instruments.keys()),
                    delta=self.cfg.kalman_delta,
                    r_var=self.cfg.kalman_r,
                    z_window=self.cfg.z_window,
                    min_warmup=min(self.cfg.min_warmup, 25),
                    z_entry=self.cfg.basket_z_entry,
                    z_exit=self.cfg.exit_z,
                    z_stop=self.cfg.stop_z,
                )
            fair_values, _ = self.basket_model.update(mids)

        # Blend pair-implied fair values into fair_values
        for pair in self.active_pairs:
            pa = mids.get(pair.stock_a)
            pb = mids.get(pair.stock_b)
            if pa is None or pb is None:
                continue
            pair.update(pa, pb)
            if pair.initialized and pair.beta > 0.1:
                fv_a = pair.alpha + pair.beta * pb
                fv_b = (pa - pair.alpha) / pair.beta
                fair_values[pair.stock_a] = 0.5 * fair_values.get(pair.stock_a, fv_a) + 0.5 * fv_a
                fair_values[pair.stock_b] = 0.5 * fair_values.get(pair.stock_b, fv_b) + 0.5 * fv_b

        # 1. Edge-only aggressive IOC (only when book is mispriced >= 3 ticks beyond fair value)
        sniped = self.executor.execute_fair_value_edge(
            fair_values,
            books,
            self.instruments,
            paused_instruments=paused,
            max_edge_lots=min(10, self.cfg.max_lots_per_pair),
            min_edge_ticks=3,
        )
        orders_sent = len(sniped)

        # 2. Passive inventory-skewed market making around fair value (skip sniped tickers to guarantee zero self-trades)
        if self.cfg.enable_passive_quoting and fair_values and self.exchange.is_connected():
            orders_sent += self.executor.quote_around_fair_value(
                fair_values,
                books,
                self.instruments,
                paused_instruments=paused | sniped,
                quote_lots=self.cfg.quote_lots,
                edge_ticks=self.cfg.quote_edge_ticks,
            )

        if self.tick_count % self.cfg.log_every_ticks == 0 and self.exchange.is_connected():
            pnl = self.exchange.get_pnl()
            pos = {k: v for k, v in self.exchange.get_positions().items() if v != 0}
            logger.info(
                "tick=%d pairs=%d paused=%d pnl=%.2f positions=%s",
                self.tick_count,
                len(self.active_pairs),
                len(paused),
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

            self.history = seed_history_from_optibook(self.cfg, list(self.instruments.keys()))
            logger.info("Trading universe: %s", sorted(self.instruments.keys()))

            while max_ticks is None or self.tick_count < max_ticks:
                self.step()
                if not self.cfg.mock_mode or max_ticks is None:
                    time.sleep(self.cfg.poll_interval_sec)
        except KeyboardInterrupt:
            logger.info("Interrupted — cancelling open orders and disconnecting")
        finally:
            for inst_id in self.instruments:
                try:
                    self.exchange.delete_orders(inst_id)
                except Exception:
                    pass
            try:
                self.exchange.disconnect()
            except Exception:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Optibook Kalman StatArb Bot")
    parser.add_argument("--mock", action="store_true", help="Run against local MockExchange")
    parser.add_argument("--max-ticks", type=int, default=None, help="Stop after N ticks (useful for testing)")
    args = parser.parse_args()

    cfg = BotConfig()
    if args.mock:
        cfg.mock_mode = True

    exchange = build_exchange(cfg)
    bot = StatArbBot(exchange, cfg)
    bot.run(max_ticks=args.max_ticks)


if __name__ == "__main__":
    main()
