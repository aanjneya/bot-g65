"""Unit and integration tests for the Optibook Kalman StatArb bot."""

from __future__ import annotations

import unittest

import numpy as np

from bot.config import BotConfig
from bot.execution import clamp_delta_for_limit, round_to_tick
from bot.kalman import BasketFairValueModel, OnlineKalmanPair, estimate_half_life, select_top_pairs
from bot.mock_exchange import MockExchange
from bot.runner import StatArbBot


class TestKalmanAndPairs(unittest.TestCase):
    def test_online_kalman_converges_and_signals(self) -> None:
        rng = np.random.default_rng(7)
        pair = OnlineKalmanPair(
            "NVDA",
            "AMD",
            min_warmup=20,
            z_window=30,
            entry_z1=1.5,
            entry_z2=2.2,
            min_hurdle_bps=1.0,
        )
        b_series = 100.0 + np.cumsum(rng.normal(0.0, 0.3, 60))
        a_series = 10.0 + 1.2 * b_series + rng.normal(0.0, 0.2, 60)

        for pa, pb in zip(a_series, b_series):
            pair.target_weight(float(pa), float(pb))

        self.assertTrue(pair.initialized)
        self.assertAlmostEqual(pair.beta, 1.2, delta=0.4)

        # Inject a moderate positive spread shock (z ~ 2.5) -> should short spread
        w = pair.target_weight(float(a_series[-1] + 0.55), float(b_series[-1]))
        self.assertLess(w, 0.0)

        # Inject an extreme outlier shock (z > stop_z) -> stop-loss flattens to 0.0
        w_stop = pair.target_weight(float(a_series[-1] + 5.0), float(b_series[-1]))
        self.assertEqual(w_stop, 0.0)

    def test_select_top_pairs_discovers_cointegrated_pair(self) -> None:
        rng = np.random.default_rng(12)
        x = 100.0 + np.cumsum(rng.normal(0.0, 0.5, 260))
        # Generate a realistic OU mean-reverting spread (phi=0.82 -> half-life ~ 3.5 ticks)
        ou = np.zeros(260)
        for t in range(1, 260):
            ou[t] = 0.82 * ou[t - 1] + rng.normal(0.0, 0.2)
        y = 5.0 + 1.5 * x + ou
        z = 50.0 + np.cumsum(rng.normal(0.0, 0.8, 260))
        history = {"NVDA": y.tolist(), "AMD": x.tolist(), "VRT": z.tolist()}

        pairs = select_top_pairs(history, max_pairs=2, min_points=40)
        self.assertGreaterEqual(len(pairs), 1)
        top = pairs[0]
        self.assertEqual({top.stock_a, top.stock_b}, {"NVDA", "AMD"})
        hl, slope = estimate_half_life(np.asarray(y) - (5.0 + 1.5 * np.asarray(x)))
        self.assertTrue(np.isfinite(hl) and slope < 0)


class TestRiskAndExecution(unittest.TestCase):
    def test_clamp_delta_respects_100_lot_limit_and_open_orders(self) -> None:
        # Holding 75 long, 10 open bid -> max additional buy is 15
        self.assertEqual(clamp_delta_for_limit(30, current_pos=75, open_bid_vol=10, max_pos=100), 15)
        # Holding -90 short, 5 open ask -> max additional sell is -5
        self.assertEqual(clamp_delta_for_limit(-20, current_pos=-90, open_ask_vol=5, max_pos=100), -5)
        # Already at limit
        self.assertEqual(clamp_delta_for_limit(10, current_pos=100, max_pos=100), 0)

    def test_round_to_tick(self) -> None:
        self.assertEqual(round_to_tick(44.613, 0.01, "bid"), 44.61)
        self.assertEqual(round_to_tick(44.628, 0.05, "ask"), 44.65)

    def test_no_self_trade_when_ioc_crosses_own_limit_order(self) -> None:
        from bot.execution import RiskAndExecution

        ex = MockExchange(universe=["VRT"], seed=1, max_pos=100)
        ex.connect()
        books = {"VRT": ex.get_last_price_book("VRT")}
        insts = ex.get_tradable_instruments()
        best_bid = books["VRT"].bids[0].price

        # Place a resting passive bid at best_bid (simulating our own quote)
        ex.insert_order("VRT", price=best_bid, volume=5, side="bid", order_type="limit")
        self.assertEqual(len(ex.get_outstanding_orders("VRT")), 1)

        # Now send an aggressive IOC sell via RiskAndExecution — must cancel resting bid first, not self-trade
        executor = RiskAndExecution(ex, max_position_lots=100)
        sent = executor.execute_target_positions({"VRT": -10}, books, insts)
        self.assertEqual(sent, 1)
        self.assertTrue(ex.is_connected())
        self.assertEqual(ex.get_positions()["VRT"], -10)


class TestMockRunner(unittest.TestCase):
    def test_end_to_end_mock_simulation(self) -> None:
        cfg = BotConfig(mock_mode=True, min_warmup=25, z_window=35, min_hurdle_bps=2.0, log_every_ticks=50)
        ex = MockExchange(universe=cfg.universe, seed=99, max_pos=cfg.max_position_lots)
        bot = StatArbBot(ex, cfg)
        bot.run(max_ticks=120)

        self.assertFalse(ex.is_connected())  # Cleanly disconnected on exit
        for inst, pos in ex.get_positions().items():
            self.assertLessEqual(abs(pos), 100, f"Position limit breached on {inst}: {pos}")

    def test_basket_model_and_news_guard(self) -> None:
        universe = ["AMD", "AVGO", "CRWV", "MU", "NBIS", "NVDA", "SNDK", "VRT"]
        basket = BasketFairValueModel(universe, min_warmup=15, z_window=25)
        rng = np.random.default_rng(5)
        base = {u: 100.0 + i * 5.0 for i, u in enumerate(universe)}
        for _ in range(30):
            mids = {u: base[u] + float(rng.normal(0.0, 0.2)) for u in universe}
            fvs, weights = basket.update(mids)
        self.assertEqual(len(fvs), len(universe))
        self.assertEqual(len(weights), len(universe))

        # Test news guard pulls open orders on mentioned ticker
        cfg = BotConfig(mock_mode=True, min_warmup=20, z_window=30, news_cooldown_ticks=10, max_requests_per_sec=1000)
        ex = MockExchange(universe=universe, seed=11, max_pos=100)
        ex.connect()
        bot = StatArbBot(ex, cfg)
        bot.instruments = ex.get_tradable_instruments()
        for _ in range(35):
            bot.step()
        ex.push_mock_news("CRWV announces unexpected guidance change")
        bot.step()
        self.assertEqual(ex.get_outstanding_orders("CRWV"), {})
        self.assertIn("CRWV", bot.news_cooldown_until)


if __name__ == "__main__":
    unittest.main()
