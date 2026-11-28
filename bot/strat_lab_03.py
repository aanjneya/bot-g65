"""Wave 2B Distinct Strategy Architectures (S8-S9) for Optibook (`bot/backtest.py` & live compatible).

Contains 2 additional orthogonal quantitative strategy architectures:
5. WaterfallHarvester (S8): Full Order-Book Depth Decomposition (Student Layer vs House Ladder)
   + Staggered Dual-Layer Passive Quoting (tight inner scalp + outer high-edge sweep catcher)
   + 8-Stock Student Mispricing Harvester.
6. ImpulseStatArb (S9): Pure Return-Space Multi-Horizon Impulse Forecaster (ZERO price-level
   cointegration or intercept alpha! Predicts future dP purely from multi-scale leader HouseMid
   changes + follower momentum + depth microprice, immune to cointegration offset shifts).
"""
from __future__ import annotations

import math
from collections import deque
from typing import Dict, List, Optional, Tuple

from strat_lab_02 import (
    EPS,
    FOLLOWERS,
    LEADERS,
    POS_LIMIT,
    TICK,
    ExactStateSpaceModel,
    QueueSmartExecMixin,
    ceil_tick,
    floor_tick,
    infer_house_mid,
    r1,
)


# ===========================================================================
# STRATEGY S8: WaterfallHarvester (Book-Layer Decomposition + Dual-Layer Quoting)
# ===========================================================================

class WaterfallHarvester(QueueSmartExecMixin):
    """S8: Decomposes every order book into Student Liquidity (inside walls) vs House Walls,
    snipes mispriced student liquidity across all 8 stocks, and deploys staggered 2-layer
    passive quotes (inner scalp + outer high-edge quote) while strictly respecting <=200 open vol
    and +-100 position limits!"""

    ORDER = ("SNDK", "NBIS", "CRWV", "AVGO", "AMD", "NVDA", "MU", "VRT")

    def __init__(self, ex, folder=None):
        self._init_exec(ex)
        self.model = ExactStateSpaceModel()

    def _student_imbalance(self, inst: str, bids: list, asks: list) -> float:
        """Compute order-book imbalance strictly from non-house student orders inside the walls!"""
        cap = 800 if inst in FOLLOWERS else 2000
        sb = sum(v for _, v in bids if v < cap)
        sa = sum(v for _, v in asks if v < cap)
        if sb + sa == 0:
            return 0.0
        return (sb - sa) / (sb + sa)

    def _sync_dual_layer(self, inst: str, pos: int, mine: dict, bid_layers: list, ask_layers: list, reserve: int = 4):
        """Sync up to 2 passive layers per side while avoiding self-trades and churn."""
        # Filter invalid crossings
        if bid_layers and ask_layers and bid_layers[0][0] >= ask_layers[0][0] - EPS:
            if pos > 0:
                bid_layers = []
            else:
                ask_layers = []

        cur_bids = sorted([o for o in mine.values() if o.side == "bid"], key=lambda x: -x.price)
        cur_asks = sorted([o for o in mine.values() if o.side == "ask"], key=lambda x: x.price)

        # Keep existing orders if top layer matches price and still has >= 10 lots
        b_match = (
            (not bid_layers and not cur_bids)
            or (
                bid_layers
                and cur_bids
                and abs(cur_bids[0].price - bid_layers[0][0]) < EPS
                and cur_bids[0].volume >= 10
                and (not ask_layers or cur_bids[0].price < ask_layers[0][0] - EPS)
            )
        )
        a_match = (
            (not ask_layers and not cur_asks)
            or (
                ask_layers
                and cur_asks
                and abs(cur_asks[0].price - ask_layers[0][0]) < EPS
                and cur_asks[0].volume >= 10
                and (not bid_layers or cur_asks[0].price > bid_layers[0][0] + EPS)
            )
        )
        if b_match and a_match:
            return

        # Cancel all on conflict or dual update
        if (not b_match and bid_layers and any(o.price <= bid_layers[0][0] + EPS for o in cur_asks)) or \
           (not a_match and ask_layers and any(o.price >= ask_layers[0][0] - EPS for o in cur_bids)) or \
           (not b_match and not a_match and len(mine) >= 2):
            if not self._cancel_all(inst, mine, reserve=reserve):
                return
            cur_bids, cur_asks = [], []
            b_match = a_match = False

        if not b_match:
            for o in cur_bids:
                if not self._use_req(reserve=reserve):
                    return
                self.ex.delete_order(inst, order_id=o.order_id)
                mine.pop(o.order_id, None)
            rem = POS_LIMIT - pos - sum(o.volume for o in mine.values() if o.side == "bid")
            for px, sz in bid_layers:
                v = min(sz, rem)
                if v > 0 and self._use_req(reserve=reserve):
                    self.ex.insert_order(inst, price=r1(px), volume=int(v), side="bid", order_type="limit")
                    rem -= v

        if not a_match:
            for o in cur_asks:
                if not self._use_req(reserve=reserve):
                    return
                self.ex.delete_order(inst, order_id=o.order_id)
                mine.pop(o.order_id, None)
            rem = POS_LIMIT + pos - sum(o.volume for o in mine.values() if o.side == "ask")
            for px, sz in ask_layers:
                v = min(sz, rem)
                if v > 0 and self._use_req(reserve=reserve):
                    self.ex.insert_order(inst, price=r1(px), volume=int(v), side="ask", order_type="limit")
                    rem -= v

    def step(self):
        books, mines = {}, {}
        for s in self.insts:
            b, a, m = self._clean_book(s)
            books[s] = (b, a)
            mines[s] = m

        f_star, f_paths, _ = self.model.update(self._now(), books)
        positions = self.ex.get_positions()

        for s in self.ORDER:
            if s not in f_star:
                continue
            bids, asks = books[s]
            if not bids or not asks:
                continue
            mine = mines[s]
            pos = int(positions.get(s, 0))
            st_imb = self._student_imbalance(s, bids, asks)
            fv = f_star[s] + 0.025 * st_imb
            if s == "SNDK" and len(f_paths[s]) >= 3:
                fv = 0.30 * f_paths[s][2] + 0.70 * fv

            is_fol = s in FOLLOWERS
            self._pull_toxic_orders(s, mine, fv, safe_margin=0.015 if is_fol else 0.03)

            bb, ba = bids[0][0], asks[0][0]
            cover_h = 0.015 if is_fol else 0.025
            open_h = 0.042 if is_fol else 0.080
            cap = POS_LIMIT if is_fol else 55

            if pos < 0 and fv - ba >= cover_h:
                pos += self._sweep_ioc(s, "bid", floor_tick(fv - cover_h), asks, pos, mine, max_target_pos=0)
            elif pos > 0 and bb - fv >= cover_h:
                pos -= self._sweep_ioc(s, "ask", ceil_tick(fv + cover_h), bids, pos, mine, max_target_pos=0)

            res = fv - (0.00075 if is_fol else 0.0025) * pos
            if res - ba >= open_h and pos < cap:
                pos += self._sweep_ioc(s, "bid", floor_tick(res - open_h), asks, pos, mine, max_target_pos=cap)
            elif bb - res >= open_h and pos > -cap:
                pos -= self._sweep_ioc(s, "ask", ceil_tick(res + open_h), bids, pos, mine, max_target_pos=cap)
            positions[s] = pos

        # Dual-layer waterfall passive quoting on followers + exit/inside-spread on leaders
        for s in self.ORDER:
            if s not in f_star or self._budget(reserve=3) <= 0:
                continue
            bids, asks = books[s]
            if not bids or not asks:
                continue
            mine = mines[s]
            pos = int(positions.get(s, 0))
            st_imb = self._student_imbalance(s, bids, asks)
            fv = f_star[s] + 0.025 * st_imb
            if s == "SNDK" and len(f_paths[s]) >= 3:
                fv = 0.30 * f_paths[s][2] + 0.70 * fv
            bb, ba = bids[0][0], asks[0][0]
            is_fol = s in FOLLOWERS
            res = fv - (0.00075 if is_fol else 0.0025) * pos

            if is_fol:
                b1 = min(r1(ba - TICK), floor_tick(res - 0.038))
                a1 = max(r1(bb + TICK), ceil_tick(res + 0.038))
                b_layers = [(b1, 60), (r1(b1 - TICK), 35)] if pos < POS_LIMIT else []
                a_layers = [(a1, 60), (r1(a1 + TICK), 35)] if pos > -POS_LIMIT else []
                if pos > 0:
                    a_layers = [(a1, max(60, pos)), (r1(a1 + TICK), 30)]
                elif pos < 0:
                    b_layers = [(b1, max(60, -pos)), (r1(b1 - TICK), 30)]
                self._sync_dual_layer(s, pos, mine, b_layers, a_layers, reserve=3)
            else:
                want_b = want_a = None
                bp = min(r1(ba - TICK), floor_tick(res - 0.09))
                ap = max(r1(bb + TICK), ceil_tick(res + 0.09))
                if pos < 45 and bp > bb + EPS:
                    want_b = (bp, min(30, 45 - pos))
                if pos > -45 and ap < ba - EPS:
                    want_a = (ap, min(30, 45 + pos))
                if pos > 0:
                    want_a = (max(r1(bb + TICK), ceil_tick(res + 0.02)), pos)
                elif pos < 0:
                    want_b = (min(r1(ba - TICK), floor_tick(res - 0.02)), -pos)
                self._sync_passive(s, pos, mine, want_b, want_a, min_keep_vol=10, hyst_ticks=0, reserve=4)


# ===========================================================================
# STRATEGY S9: ImpulseStatArb (Pure Return-Space Multi-Horizon Impulse Engine)
# ===========================================================================

class ImpulseStatArb(QueueSmartExecMixin):
    """S9: Pure Return-Space Multi-Horizon Impulse Engine — NO price-level regression or
    cointegration intercept alpha_t at all!
    Forecasts future price change E[dP_follower] purely from:
      beta * (HouseMid_leader(t) - LaggedEMA_leader(t))
    anchored directly onto current HouseMid_follower(t)!
    Mathematically invariant to any shift in static price offset!"""

    SPECS = {
        "SNDK": ("NVDA", 0.425, 2.40, 0.50),
        "NBIS": ("AVGO", 0.407, 0.40, 1.00),
        "CRWV": ("MU",   1.121, 0.00, 1.00),
    }

    def __init__(self, ex, folder=None):
        self._init_exec(ex)
        self.lead_hist = {s: deque(maxlen=60) for s in LEADERS}
        self.lead_ema = {s: {} for s in LEADERS}
        self.last_t = None

    def step(self):
        now = self._now()
        dt = 0.20 if self.last_t is None else max(0.05, min(1.0, now - self.last_t))
        self.last_t = now

        books, mines, hmids, micro = {}, {}, {}, {}
        for s in self.insts:
            b, a, m = self._clean_book(s)
            books[s] = (b, a)
            mines[s] = m
            if b and a:
                hmids[s], _, _ = infer_house_mid(s, b, a)
                bb, bv = b[0]
                ba, av = a[0]
                bvc, avc = min(bv, 100), min(av, 100)
                micro[s] = (bb * avc + ba * bvc) / max(1, bvc + avc) - 0.5 * (bb + ba)

        for lead in LEADERS:
            if lead not in hmids:
                continue
            hm = hmids[lead]
            for hl in (0.50, 1.00):
                a = 1.0 - math.exp(-dt * math.log(2.0) / hl)
                self.lead_ema[lead][hl] = (1.0 - a) * self.lead_ema[lead].get(hl, hm) + a * hm
            self.lead_hist[lead].append((now, hm, self.lead_ema[lead][0.50], self.lead_ema[lead][1.00]))

        positions = self.ex.get_positions()

        for s, (lead, beta, lag_s, hl_s) in self.SPECS.items():
            if s not in hmids or lead not in hmids:
                continue
            hist = self.lead_hist[lead]
            if not hist:
                continue
            col = 2 if hl_s == 0.50 else 3
            lag_ema = hist[0][col]
            for rec in reversed(hist):
                if rec[0] <= now - lag_s + 1e-6:
                    lag_ema = rec[col]
                    break

            # Pure return/difference impulse anchored on current follower HouseMid (no alpha parameter!)
            expected_impulse = beta * (hmids[lead] - lag_ema)
            fv = hmids[s] + expected_impulse + 0.18 * micro.get(s, 0.0)

            bids, asks = books[s]
            mine = mines[s]
            pos = int(positions.get(s, 0))
            self._pull_toxic_orders(s, mine, fv, safe_margin=0.015)

            bb, ba = bids[0][0], asks[0][0]
            if pos < 0 and fv - ba >= 0.015:
                pos += self._sweep_ioc(s, "bid", floor_tick(fv - 0.015), asks, pos, mine, max_target_pos=0)
            elif pos > 0 and bb - fv >= 0.015:
                pos -= self._sweep_ioc(s, "ask", ceil_tick(fv + 0.015), bids, pos, mine, max_target_pos=0)

            res = fv - 0.0008 * pos
            if res - ba >= 0.045 and pos < POS_LIMIT:
                pos += self._sweep_ioc(s, "bid", floor_tick(res - 0.045), asks, pos, mine, max_target_pos=POS_LIMIT)
            elif bb - res >= 0.045 and pos > -POS_LIMIT:
                pos -= self._sweep_ioc(s, "ask", ceil_tick(res + 0.045), bids, pos, mine, max_target_pos=POS_LIMIT)

            if self._budget(reserve=3) > 0:
                res = fv - 0.0008 * pos
                bid_px = min(r1(ba - TICK), floor_tick(res - 0.04))
                ask_px = max(r1(bb + TICK), ceil_tick(res + 0.04))
                b_sz = min(85, POS_LIMIT - pos) if pos < POS_LIMIT else 0
                a_sz = min(85, POS_LIMIT + pos) if pos > -POS_LIMIT else 0
                self._sync_passive(
                    s, pos, mine,
                    (bid_px, b_sz) if b_sz > 0 else None,
                    (ask_px, a_sz) if a_sz > 0 else None,
                    min_keep_vol=14, hyst_ticks=0, reserve=3,
                )
