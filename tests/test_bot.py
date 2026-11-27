"""Unit and integration tests for the Optibook Kalman StatArb bot."""

from __future__ import annotations

import unittest

import numpy as np

from bot.config import BotConfig
from bot.execution import clamp_delta_for_limit, round_to_tick
from bot.kalman import OnlineKalmanPair, estimate_half_life, select_top_pairs
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
        x = 100.0 + np.cumsum(rng.normal(0.0, 0.5, 120))
        y = 5.0 + 1.5 * x + rng.normal(0.0, 0.2, 120)
        z = 50.0 + np.cumsum(rng.normal(0.0, 0.8, 120))
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


class TestMockRunner(unittest.TestCase):
    def test_end_to_end_mock_simulation(self) -> None:
        cfg = BotConfig(mock_mode=True, min_warmup=25, z_window=35, min_hurdle_bps=2.0, log_every_ticks=50)
        ex = MockExchange(universe=cfg.universe, seed=99, max_pos=cfg.max_position_lots)
        bot = StatArbBot(ex, cfg)
        bot.run(max_ticks=120)

        self.assertFalse(ex.is_connected())  # Cleanly disconnected on exit
        for inst, pos in ex.get_positions().items():
            self.assertLessEqual(abs(pos), 100, f"Position limit breached on {inst}: {pos}")


if __name__ == "__main__":
    unittest.main()
