"""Unit and integration tests for the Optibook ABMM-S trading bot."""

from __future__ import annotations

import csv
import os
import tempfile
import unittest
from types import SimpleNamespace

import numpy as np

from bot.config import BotConfig
from bot.execution import RiskAndExecution, clamp_delta_for_limit, round_to_tick
from bot.kalman import BasketFairValueModel, OnlineKalmanPair, estimate_half_life, select_top_pairs
from bot.mock_exchange import MockExchange
from bot.runner import ABMMSBot, StatArbBot
from bot.signals import (
    ABMMSAlphaEngine,
    CounterpartyTracker,
    MicropriceOFI,
    NewsSentimentEngine,
    ReturnLeadLagCovariance,
    compute_endgame_state,
    extract_wall_anchor,
)


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

        w = pair.target_weight(float(a_series[-1] + 0.55), float(b_series[-1]))
        self.assertLess(w, 0.0)

        w_stop = pair.target_weight(float(a_series[-1] + 5.0), float(b_series[-1]))
        self.assertEqual(w_stop, 0.0)

    def test_select_top_pairs_discovers_cointegrated_pair(self) -> None:
        rng = np.random.default_rng(12)
        x = 100.0 + np.cumsum(rng.normal(0.0, 0.5, 260))
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


class TestABMMSSignals(unittest.TestCase):
    def test_microprice_ofi_and_edge_cases(self) -> None:
        mo = MicropriceOFI()
        # Empty / crossed books return (None, 0.0) safely
        empty_book = SimpleNamespace(bids=[], asks=[])
        self.assertEqual(mo.update("NVDA", empty_book, 0.10), (None, 0.0))

        # Heavy bid volume vs thin ask volume -> positive microprice edge
        book1 = SimpleNamespace(
            bids=[SimpleNamespace(price=100.0, volume=90)],
            asks=[SimpleNamespace(price=100.2, volume=10)],
        )
        mid1, imp1 = mo.update("NVDA", book1, 0.10)
        self.assertAlmostEqual(mid1, 100.1)
        self.assertGreater(imp1, 0.0)

        # Bid steps up & ask thins -> positive OFI adds upward impulse
        book2 = SimpleNamespace(
            bids=[SimpleNamespace(price=100.1, volume=120)],
            asks=[SimpleNamespace(price=100.3, volume=5)],
        )
        mid2, imp2 = mo.update("NVDA", book2, 0.10)
        self.assertAlmostEqual(mid2, 100.2)
        self.assertGreater(imp2, 0.0)

    def test_wall_anchor_ignores_small_orders_and_single_sided_walls(self) -> None:
        # Student orders (<=200) ignored
        small_book = SimpleNamespace(
            bids=[SimpleNamespace(price=100.0, volume=180)],
            asks=[SimpleNamespace(price=100.4, volume=180)],
        )
        self.assertEqual(extract_wall_anchor(small_book, 100.2, 0.10, wall_min_volume=250), 0.0)

        # Single-sided wall returns 0.0 (never distorts FV)
        one_sided = SimpleNamespace(
            bids=[SimpleNamespace(price=100.0, volume=600)],
            asks=[SimpleNamespace(price=100.4, volume=50)],
        )
        self.assertEqual(extract_wall_anchor(one_sided, 100.2, 0.10, wall_min_volume=250), 0.0)

        # Two-sided house-bot walls shifted above current mid -> positive wall anchor
        walled = SimpleNamespace(
            bids=[
                SimpleNamespace(price=100.0, volume=20),
                SimpleNamespace(price=99.9, volume=500),
            ],
            asks=[
                SimpleNamespace(price=100.2, volume=20),
                SimpleNamespace(price=100.7, volume=500),
            ],
        )
        # mid=100.1, wall_mid=(99.9+100.7)/2 = 100.3 -> +0.2 shift
        gap = extract_wall_anchor(walled, 100.1, 0.10, wall_min_volume=250)
        self.assertAlmostEqual(gap, 0.2, places=6)

    def test_counterparty_markout_classifies_informed_vs_noise(self) -> None:
        ct = CounterpartyTracker(horizon_sec=5.0, min_trades=10, own_username="Team65")
        t = 100.0
        # 12 informed buys by SmartBot where mid rises +0.30 (3 ticks) 5s later
        for i in range(12):
            ct.on_trade(t + i, "NVDA", 100.0, 10, "bid", "SmartBot", "MM", tick=0.10)
            ct.update_markouts(t + i + 5.0, {"NVDA": 100.3})
        self.assertEqual(ct.classify("SmartBot"), "informed")

        # 12 noise buys by NoiseBot where mid drops -0.20 (-2 ticks) 5s later
        for i in range(12):
            ct.on_trade(t + 20 + i, "NVDA", 100.0, 10, "bid", "NoiseBot", "MM", tick=0.10)
            ct.update_markouts(t + 20 + i + 5.0, {"NVDA": 99.8})
        self.assertEqual(ct.classify("NoiseBot"), "noise")

        # Own trades are ignored
        ct.on_trade(t + 50, "NVDA", 100.0, 10, "bid", "Team65", "MM", tick=0.10)
        self.assertNotIn("Team65", ct.stats)

        # Informed aggressor triggers positive impulse & widened spread
        ct.on_trade(t + 60, "NVDA", 100.0, 20, "bid", "SmartBot", "MM", tick=0.10)
        imp, sm = ct.get_impulse_and_spread_mult(t + 60, "NVDA")
        self.assertGreater(imp, 0.0)
        self.assertGreater(sm, 1.0)

        # Impulse decays smoothly over time
        imp_later, _ = ct.get_impulse_and_spread_mult(t + 70, "NVDA")
        self.assertLess(abs(imp_later), abs(imp))

    def test_return_lead_lag_and_multi_asset_glft_cross_hedge(self) -> None:
        universe = ["NVDA", "AMD", "AVGO"]
        rlc = ReturnLeadLagCovariance(universe, min_warmup=5)
        ticks = {u: 0.10 for u in universe}

        # Feed correlated upward/downward co-movements so off-diagonal covariance is positive
        rng = np.random.default_rng(42)
        p = {"NVDA": 100.0, "AMD": 100.0, "AVGO": 100.0}
        for _ in range(40):
            common = float(rng.normal(0.0, 0.004))
            for u in universe:
                p[u] *= float(np.exp(common + rng.normal(0.0, 0.0005)))
            rlc.update(p)

        # NVDA & AVGO jump +1% while AMD hasn't moved yet -> AMD gets positive lead-lag impulse
        p["NVDA"] *= 1.01
        p["AVGO"] *= 1.01
        rlc.update(p)
        imp = rlc.lead_lag_impulses(p, ticks)
        self.assertGreater(imp["AMD"], 0.0)

        # Holding long +50 NVDA and 0 AMD/AVGO must negatively skew BOTH NVDA and correlated peers (passive cross-hedge)
        skews = rlc.glft_skew(p, {"NVDA": 50, "AMD": 0, "AVGO": 0}, gamma=0.05, ticks=ticks)
        self.assertLess(skews["NVDA"], 0.0)
        self.assertLess(skews["AMD"], 0.0)
        self.assertLess(skews["AVGO"], 0.0)
        self.assertLess(skews["NVDA"], skews["AMD"])  # Direct inventory skew stronger than cross-hedge skew

    def test_news_sentiment_engine(self) -> None:
        eng = NewsSentimentEngine(["NVDA", "AMD", "CRWV", "VRT"])
        self.assertEqual(eng.parse("NVIDIA beats revenue expectations and raises guidance"), (["NVDA"], 1))
        self.assertEqual(eng.parse("CRWV misses guidance amid investigation"), (["CRWV"], -1))
        # Negation neutralizes snipe but still returns target for quote pull
        self.assertEqual(eng.parse("NVDA denies recall rumor"), (["NVDA"], 0))
        # Sector-wide news targets entire universe
        targets, sent = eng.parse("Massive AI boom accelerates across data centers")
        self.assertEqual(set(targets), {"NVDA", "AMD", "CRWV", "VRT"})
        self.assertEqual(sent, 1)

    def test_endgame_glidepath(self) -> None:
        s_disabled = compute_endgame_state(1790.0, session_duration_sec=0.0)
        self.assertEqual(s_disabled.gamma_mult, 1.0)
        self.assertFalse(s_disabled.should_flatten)

        s_mid = compute_endgame_state(1500.0, session_duration_sec=1800.0)
        self.assertEqual(s_mid.gamma_mult, 1.0)
        self.assertEqual(s_mid.inv_inc_scale, 1.0)
        self.assertFalse(s_mid.should_flatten)

        s_glide = compute_endgame_state(1710.0, session_duration_sec=1800.0)
        self.assertGreater(s_glide.gamma_mult, 1.0)
        self.assertLess(s_glide.inv_inc_scale, 1.0)
        self.assertFalse(s_glide.should_flatten)

        s_flat = compute_endgame_state(1760.0, session_duration_sec=1800.0)
        self.assertEqual(s_flat.inv_inc_scale, 0.0)
        self.assertTrue(s_flat.should_flatten)


class TestRiskAndExecution(unittest.TestCase):
    def test_clamp_delta_respects_100_lot_limit_and_open_orders(self) -> None:
        self.assertEqual(clamp_delta_for_limit(30, current_pos=75, open_bid_vol=10, max_pos=100), 15)
        self.assertEqual(clamp_delta_for_limit(-20, current_pos=-90, open_ask_vol=5, max_pos=100), -5)
        self.assertEqual(clamp_delta_for_limit(10, current_pos=100, max_pos=100), 0)

    def test_round_to_tick(self) -> None:
        self.assertEqual(round_to_tick(44.613, 0.01, "bid"), 44.61)
        self.assertEqual(round_to_tick(44.628, 0.05, "ask"), 44.65)
        self.assertEqual(round_to_tick(44.65, 0.10, "bid"), 44.6)
        self.assertEqual(round_to_tick(44.65, 0.10, "ask"), 44.7)

    def test_quote_steps_inside_wide_spread_and_preserves_unchanged_side(self) -> None:
        ex = MockExchange(universe=["CRWV"], seed=1, max_pos=100)
        ex.connect()
        ex._prices["CRWV"] = 100.3
        insts = ex.get_tradable_instruments()
        insts["CRWV"] = SimpleNamespace(tick_size=0.10, price_limit=None)
        wide_book = SimpleNamespace(
            bids=[SimpleNamespace(price=100.0, volume=800)],
            asks=[SimpleNamespace(price=100.6, volume=800)],
        )
        executor = RiskAndExecution(ex, max_position_lots=100, max_requests_per_sec=100)
        executor.quote_around_fair_value({"CRWV": 100.3}, {"CRWV": wide_book}, insts, set(), quote_lots=5, edge_ticks=1)
        orders = list(ex.get_outstanding_orders("CRWV").values())
        bid_o = next(o for o in orders if o.side == "bid")
        ask_o = next(o for o in orders if o.side == "ask")
        self.assertGreater(bid_o.price, 100.0)
        self.assertLess(ask_o.price, 100.6)

        bid_id_before = bid_o.order_id
        executor.quote_around_fair_value({"CRWV": 100.36}, {"CRWV": wide_book}, insts, set(), quote_lots=5, edge_ticks=1)
        orders_after = ex.get_outstanding_orders("CRWV")
        self.assertIn(bid_id_before, orders_after)

    def test_no_self_trade_when_ioc_crosses_own_limit_order(self) -> None:
        ex = MockExchange(universe=["VRT"], seed=1, max_pos=100)
        ex.connect()
        books = {"VRT": ex.get_last_price_book("VRT")}
        insts = ex.get_tradable_instruments()
        best_bid = books["VRT"].bids[0].price

        ex.insert_order("VRT", price=best_bid, volume=5, side="bid", order_type="limit")
        self.assertEqual(len(ex.get_outstanding_orders("VRT")), 1)

        executor = RiskAndExecution(ex, max_position_lots=100)
        sent = executor.execute_target_positions({"VRT": -10}, books, insts)
        self.assertEqual(sent, 1)
        self.assertTrue(ex.is_connected())
        self.assertEqual(ex.get_positions()["VRT"], -10)

    def test_portfolio_open_volume_cap_and_rate_limit_no_self_trade(self) -> None:
        universe = ["AMD", "AVGO", "CRWV", "MU", "NBIS", "NVDA", "SNDK", "VRT"]
        ex = MockExchange(universe=universe, seed=3, max_pos=100)
        ex.connect()
        insts = ex.get_tradable_instruments()
        # Tight portfolio open volume cap (25 lots total across 8 stocks)
        executor = RiskAndExecution(ex, max_position_lots=100, max_open_order_volume=25, max_requests_per_sec=100)
        books = {u: ex.get_last_price_book(u) for u in universe}
        fvs = {u: ex._prices[u] for u in universe}
        executor.quote_around_fair_value(fvs, books, insts, set(), quote_lots=5, edge_ticks=1)

        total_open = sum(o.volume for u in universe for o in ex.get_outstanding_orders(u).values())
        self.assertLessEqual(total_open, 25)
        self.assertTrue(ex.is_connected())

        # Now hammer with very tight rate-limit (4 req/s) while shifting BBO sharply -> must NEVER self-trade
        tight_exec = RiskAndExecution(ex, max_position_lots=100, max_open_order_volume=180, max_requests_per_sec=4)
        for _ in range(10):
            ex.step_market()
            for u in universe:
                ex._prices[u] += 0.50
            books = {u: ex.get_last_price_book(u) for u in universe}
            fvs = {u: ex._prices[u] for u in universe}
            tight_exec.quote_around_fair_value(fvs, books, insts, set(), quote_lots=5, edge_ticks=1)
        self.assertTrue(ex.is_connected())


class TestMockRunner(unittest.TestCase):
    def test_end_to_end_mock_simulation(self) -> None:
        cfg = BotConfig(mock_mode=True, log_every_ticks=50, max_requests_per_sec=200)
        ex = MockExchange(universe=cfg.universe, seed=99, max_pos=cfg.max_position_lots)
        bot = StatArbBot(ex, cfg)
        bot.run(max_ticks=120)

        self.assertFalse(ex.is_connected())
        for inst, pos in ex.get_positions().items():
            self.assertLessEqual(abs(pos), 100, f"Position limit breached on {inst}: {pos}")

    def test_basket_model_and_news_guard_and_sniper(self) -> None:
        universe = ["AMD", "AVGO", "CRWV", "MU", "NBIS", "NVDA", "SNDK", "VRT"]
        basket = BasketFairValueModel(universe, min_warmup=15, z_window=25)
        rng = np.random.default_rng(5)
        base = {u: 100.0 + i * 5.0 for i, u in enumerate(universe)}
        for _ in range(30):
            mids = {u: base[u] + float(rng.normal(0.0, 0.2)) for u in universe}
            fvs, weights = basket.update(mids)
        self.assertEqual(len(fvs), len(universe))
        self.assertEqual(len(weights), len(universe))

        # Test neutral news pulls open orders on mentioned ticker without sniping
        cfg = BotConfig(mock_mode=True, news_cooldown_ticks=10, max_requests_per_sec=1000)
        ex = MockExchange(universe=universe, seed=11, max_pos=100)
        ex.connect()
        bot = ABMMSBot(ex, cfg)
        bot.instruments = ex.get_tradable_instruments()
        for _ in range(15):
            bot.step()
        ex.push_mock_news("CRWV announces unexpected guidance change")
        bot.step()
        self.assertEqual(ex.get_outstanding_orders("CRWV"), {})
        self.assertIn("CRWV", bot.news_cooldown_until)

        # Test bullish news fires bounded buy IOC, pulls resting quotes, and stays connected
        pos_before = ex.get_positions()["NVDA"]
        ex.push_mock_news("NVIDIA beats revenue expectations and raises guidance")
        bot.step()
        self.assertGreater(ex.get_positions()["NVDA"], pos_before)
        self.assertEqual(ex.get_outstanding_orders("NVDA"), {})
        self.assertTrue(ex.is_connected())

    def test_endgame_flatten_and_embedded_csv_logger(self) -> None:
        universe = ["NVDA", "AMD"]
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = BotConfig(
                mock_mode=True,
                universe=universe,
                session_duration_sec=1800.0,
                endgame_start_sec=180.0,
                flatten_start_sec=45.0,
                max_requests_per_sec=1000,
                enable_csv_logging=True,
                csv_log_dir=tmpdir,
            )
            ex = MockExchange(universe=universe, seed=7, max_pos=100)
            ex.connect()
            bot = ABMMSBot(ex, cfg)
            bot.instruments = ex.get_tradable_instruments()
            from bot.runner import MarketLogger

            bot.market_logger = MarketLogger(tmpdir)
            try:
                # Start session at t=0 and build a position via bullish news snipe
                bot.step(now=0.0)
                ex.push_mock_news("NVIDIA beats revenue expectations")
                bot.step(now=1.0)
                self.assertGreater(ex.get_positions()["NVDA"], 0)

                # Jump to endgame flatten window (t=1770s, 30s remaining) -> must flatten to 0
                bot.step(now=1770.0)
                self.assertEqual(ex.get_positions()["NVDA"], 0)
                self.assertEqual(ex.get_outstanding_orders("NVDA"), {})
                self.assertTrue(ex.is_connected())
            finally:
                bot.market_logger.close()

            # Verify embedded CSV logs match collector.py schema
            for fname in ("books.csv", "trades.csv", "news.csv"):
                fpath = os.path.join(tmpdir, fname)
                self.assertTrue(os.path.exists(fpath))
                with open(fpath, "r", encoding="utf-8") as f:
                    rows = list(csv.reader(f))
                self.assertGreaterEqual(len(rows), 2, f"{fname} should have header + data rows")


if __name__ == "__main__":
    unittest.main()
