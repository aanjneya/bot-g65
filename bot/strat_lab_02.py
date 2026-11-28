"""Wave 2 Distinct Strategy Architectures (S4-S7) for Optibook (`bot/backtest.py` & live compatible).

Contains 4 genuinely distinct quantitative strategy architectures:
1. StateSpaceTrajectoryArb (S4): Exact LivelyLasagnas HouseMid deconvolver (lag + EMA state space)
   with asymmetric cover/open IOC sweeps + queue-preserving hysteresis passive quoting.
2. DelayWaveRider (S5): Reconstructs the full forward trajectory {F_hat(t + tau)} over [0.2s, 2.4s]
   from the leader's historical delay buffer; times entries on short-horizon sign agreement and
   places passive take-profit limits at the predicted wave extremum.
3. StructuralGLFTMaker (S6): All-8-stock Guéant-Lehalle-Fernandez-Tapia (GLFT) market maker
   anchored on structural 3000/1000 house ladders across ALL 8 stocks with non-linear inventory
   skew, thin-queue inside-stepping, and 8-stock student-quote mispricing sweeps.
4. KalmanInnovationEngine (S7): Zero-hardcoded-beta 2D Online Recursive Kalman Filter learning
   (alpha_t, beta_t) dynamically from scratch + live TradeTick aggressor order-flow fusion.
"""
from __future__ import annotations

import math
import time
from collections import deque
from typing import Dict, List, Optional, Tuple

import numpy as np

TICK = 0.1
EPS = 1e-9
POS_LIMIT = 100
LEADERS = {"AMD", "AVGO", "MU", "NVDA", "VRT"}
FOLLOWERS = {"CRWV", "NBIS", "SNDK"}


def r1(x: float) -> float:
    return round(x + 0.0, 1)


def floor_tick(x: float) -> float:
    return round(math.floor(x / TICK + 1e-9) * TICK, 1)


def ceil_tick(x: float) -> float:
    return round(math.ceil(x / TICK - 1e-9) * TICK, 1)


def infer_house_mid(inst: str, bids: List[Tuple[float, int]], asks: List[Tuple[float, int]]) -> Tuple[float, Optional[float], Optional[float]]:
    """Extract noise-free LivelyLasagnas ladder midpoint (3000/2000/1000 on leaders, 1000/750/500 on followers)."""
    bbo_mid = 0.5 * (bids[0][0] + asks[0][0])
    if inst in LEADERS:
        wb = next((p for p, v in bids if v >= 2200), None)
        if wb is None:
            wb2 = next((p for p, v in bids if v >= 1500), None)
            if wb2 is not None:
                wb = r1(wb2 + 0.1)
            else:
                wb3 = next((p for p, v in bids if v >= 850), None)
                if wb3 is not None:
                    wb = r1(wb3 + 0.2)
        wa = next((p for p, v in asks if v >= 2200), None)
        if wa is None:
            wa2 = next((p for p, v in asks if v >= 1500), None)
            if wa2 is not None:
                wa = r1(wa2 - 0.1)
            else:
                wa3 = next((p for p, v in asks if v >= 850), None)
                if wa3 is not None:
                    wa = r1(wa3 - 0.2)
    else:
        wb = next((p for p, v in bids if v >= 850), None)
        if wb is None:
            wb2 = next((p for p, v in bids if v >= 620), None)
            if wb2 is not None:
                wb = r1(wb2 + 0.1)
            else:
                wb3 = next((p for p, v in bids if v >= 420), None)
                if wb3 is not None:
                    wb = r1(wb3 + 0.2)
        wa = next((p for p, v in asks if v >= 850), None)
        if wa is None:
            wa2 = next((p for p, v in asks if v >= 620), None)
            if wa2 is not None:
                wa = r1(wa2 - 0.1)
            else:
                wa3 = next((p for p, v in asks if v >= 420), None)
                if wa3 is not None:
                    wa = r1(wa3 - 0.2)

    if wb is not None and wa is not None and wa > wb:
        return 0.5 * (wb + wa), wb, wa
    return bbo_mid, wb, wa


class QueueSmartExecMixin:
    """High-efficiency execution engine:
    - Strictly <= 23 req/s sliding window (never hits Optibook's 25 req/s disconnect)
    - Priority reserve so IOCs and toxic-quote cancels ALWAYS execute immediately
    - Preserves queue priority on partially-filled resting orders (never cancels front-of-queue just to top up!)
    - Uses amend_order when reducing volume at the same price
    - Guarantees zero self-trades
    """

    MAX_REQ_PER_SEC = 23

    def _init_exec(self, ex):
        self.ex = ex
        self.insts = list(getattr(ex, "instruments", [])) or sorted(ex.get_tradable_instruments().keys())
        self.req_ts: deque = deque()

    def _now(self) -> float:
        clk = getattr(self.ex, "clock", None)
        return clk.time() if clk is not None else time.time()

    def _budget(self, reserve: int = 0) -> int:
        now = self._now()
        while self.req_ts and now - self.req_ts[0] >= 1.0 - 1e-9:
            self.req_ts.popleft()
        return max(0, self.MAX_REQ_PER_SEC - reserve - len(self.req_ts))

    def _use_req(self, reserve: int = 0) -> bool:
        if self._budget(reserve) <= 0:
            return False
        self.req_ts.append(self._now())
        return True

    def _clean_book(self, inst: str):
        raw = self.ex.get_last_price_book(inst)
        mine = self.ex.get_outstanding_orders(inst)
        if raw is None:
            return [], [], mine
        own_b, own_a = {}, {}
        for o in mine.values():
            d = own_b if o.side == "bid" else own_a
            p = r1(o.price)
            d[p] = d.get(p, 0) + int(o.volume)
        bids = [(r1(l.price), int(l.volume) - own_b.get(r1(l.price), 0)) for l in (raw.bids or [])]
        asks = [(r1(l.price), int(l.volume) - own_a.get(r1(l.price), 0)) for l in (raw.asks or [])]
        return [x for x in bids if x[1] > 0], [x for x in asks if x[1] > 0], mine

    def _cancel_all(self, inst: str, mine: dict, reserve: int = 0) -> bool:
        if not mine:
            return True
        if not self._use_req(reserve):
            return False
        self.ex.delete_orders(inst)
        mine.clear()
        return True

    def _pull_toxic_orders(self, inst: str, mine: dict, fv: float, safe_margin: float = 0.01):
        """Immediately cancel any resting order that is now adverse to updated Fair Value!"""
        if not mine:
            return
        bad_b = [o for o in mine.values() if o.side == "bid" and o.price >= fv - safe_margin]
        bad_a = [o for o in mine.values() if o.side == "ask" and o.price <= fv + safe_margin]
        if bad_b and bad_a:
            self._cancel_all(inst, mine, reserve=0)
        else:
            for o in bad_b + bad_a:
                if self._use_req(reserve=0):
                    self.ex.delete_order(inst, order_id=o.order_id)
                    mine.pop(o.order_id, None)

    def _sweep_ioc(self, inst: str, side: str, limit_px: float, levels: list, pos: int, mine: dict, max_target_pos: int = POS_LIMIT) -> int:
        """Sweep all book liquidity up to limit_px, respecting +-max_target_pos and zero self-trade."""
        if side == "bid":
            avail = sum(v for p, v in levels if p <= limit_px + EPS)
            desired = max_target_pos - pos
        else:
            avail = sum(v for p, v in levels if p >= limit_px - EPS)
            desired = max_target_pos + pos
        vol = min(avail, desired)
        if vol <= 0:
            return 0

        opp = "ask" if side == "bid" else "bid"
        conflict = any(
            o.side == opp and ((side == "bid" and o.price <= limit_px + EPS) or (side == "ask" and o.price >= limit_px - EPS))
            for o in mine.values()
        )
        same_open = sum(o.volume for o in mine.values() if o.side == side)
        hard_headroom = (POS_LIMIT - pos - same_open) if side == "bid" else (POS_LIMIT + pos - same_open)
        if conflict or (hard_headroom < vol and same_open > 0):
            if not self._cancel_all(inst, mine, reserve=0):
                return 0
            same_open = 0

        hard_headroom = (POS_LIMIT - pos - same_open) if side == "bid" else (POS_LIMIT + pos - same_open)
        v = min(vol, hard_headroom)
        if v <= 0 or not self._use_req(reserve=0):
            return 0
        resp = self.ex.insert_order(inst, price=r1(limit_px), volume=int(v), side=side, order_type="ioc")
        return int(v) if getattr(resp, "success", False) else 0

    def _sync_passive(
        self,
        inst: str,
        pos: int,
        mine: dict,
        want_bid: Optional[Tuple[float, int]],
        want_ask: Optional[Tuple[float, int]],
        min_keep_vol: int = 8,
        hyst_ticks: int = 0,
        reserve: int = 4,
    ):
        """Queue-preserving passive sync: keeps front-of-queue partial orders alive instead of churning!"""
        if want_bid and want_bid[1] <= 0:
            want_bid = None
        if want_ask and want_ask[1] <= 0:
            want_ask = None
        if want_bid and want_ask and want_bid[0] >= want_ask[0] - EPS:
            if pos > 0:
                want_bid = None
            elif pos < 0:
                want_ask = None
            else:
                want_bid = None

        cur_bids = [o for o in mine.values() if o.side == "bid"]
        cur_asks = [o for o in mine.values() if o.side == "ask"]

        def side_ok(cur_list, want, side: str) -> bool:
            if want is None:
                return len(cur_list) == 0
            if len(cur_list) != 1:
                return False
            o = cur_list[0]
            px_diff = abs(o.price - want[0])
            if px_diff > hyst_ticks * TICK + EPS:
                return False
            # Never cross opposite wanted quote
            if side == "bid" and want_ask and o.price >= want_ask[0] - EPS:
                return False
            if side == "ask" and want_bid and o.price <= want_bid[0] + EPS:
                return False
            # Keep partial order if it still has meaningful size (preserves queue=0!)
            return o.volume >= min(min_keep_vol, want[1])

        b_ok = side_ok(cur_bids, want_bid, "bid")
        a_ok = side_ok(cur_asks, want_ask, "ask")
        if b_ok and a_ok:
            return

        # Prevent self-trade on update
        if not b_ok and want_bid and any(o.price <= want_bid[0] + EPS for o in cur_asks):
            if not self._cancel_all(inst, mine, reserve=reserve):
                return
            cur_bids, cur_asks = [], []
            b_ok = a_ok = False
        if not a_ok and want_ask and any(o.price >= want_ask[0] - EPS for o in cur_bids):
            if not self._cancel_all(inst, mine, reserve=reserve):
                return
            cur_bids, cur_asks = [], []
            b_ok = a_ok = False

        if not b_ok and not a_ok and len(mine) >= 2:
            if not self._cancel_all(inst, mine, reserve=reserve):
                return
            cur_bids, cur_asks = [], []

        if not b_ok:
            # If single order at exact wanted price has TOO MUCH volume, amend_order preserves queue!
            if want_bid and len(cur_bids) == 1 and abs(cur_bids[0].price - want_bid[0]) < EPS and cur_bids[0].volume > want_bid[1]:
                if self._use_req(reserve=reserve):
                    self.ex.amend_order(inst, order_id=cur_bids[0].order_id, volume=int(want_bid[1]))
                    cur_bids[0].volume = int(want_bid[1])
            else:
                for o in list(cur_bids):
                    if not self._use_req(reserve=reserve):
                        return
                    self.ex.delete_order(inst, order_id=o.order_id)
                    mine.pop(o.order_id, None)
                if want_bid:
                    open_b = sum(o.volume for o in mine.values() if o.side == "bid")
                    v = min(want_bid[1], POS_LIMIT - pos - open_b)
                    if v > 0 and self._use_req(reserve=reserve):
                        self.ex.insert_order(inst, price=r1(want_bid[0]), volume=int(v), side="bid", order_type="limit")

        if not a_ok:
            if want_ask and len(cur_asks) == 1 and abs(cur_asks[0].price - want_ask[0]) < EPS and cur_asks[0].volume > want_ask[1]:
                if self._use_req(reserve=reserve):
                    self.ex.amend_order(inst, order_id=cur_asks[0].order_id, volume=int(want_ask[1]))
                    cur_asks[0].volume = int(want_ask[1])
            else:
                for o in list(cur_asks):
                    if not self._use_req(reserve=reserve):
                        return
                    self.ex.delete_order(inst, order_id=o.order_id)
                    mine.pop(o.order_id, None)
                if want_ask:
                    open_a = sum(o.volume for o in mine.values() if o.side == "ask")
                    v = min(want_ask[1], POS_LIMIT + pos - open_a)
                    if v > 0 and self._use_req(reserve=reserve):
                        self.ex.insert_order(inst, price=r1(want_ask[0]), volume=int(v), side="ask", order_type="limit")


class ExactStateSpaceModel:
    """Deconvolves LivelyLasagnas's exact HouseMid delay + EMA equations:
    - SNDK <- NVDA: beta=0.4250, lag=2.40s, EMA HL=0.50s
    - NBIS <- AVGO: beta=0.4069, lag=0.40s, EMA HL=1.00s
    - CRWV <- MU  : beta=1.1214, lag=0.00s, EMA HL=1.00s
    Reconstructs:
      1) alpha_t (zero-lag-bias offset calibrated against lagged-EMA leader!)
      2) F_star(t): asymptotic fair value alpha_t + beta * HouseMid_lead(t)
      3) F_path(t): full future trajectory [F(t+0.4s), F(t+0.8s), ..., F(t+2.4s)]!
    """

    SPECS = {
        "SNDK": ("NVDA", 0.42504, 2.40, 0.50),
        "NBIS": ("AVGO", 0.40687, 0.40, 1.00),
        "CRWV": ("MU",   1.12137, 0.00, 1.00),
    }

    def __init__(self):
        self.lead_hist: Dict[str, deque] = {s: deque(maxlen=60) for s in LEADERS}
        self.lead_ema: Dict[str, Dict[float, float]] = {s: {} for s in LEADERS}
        self.alpha: Dict[str, float] = {}
        self.last_t: Optional[float] = None

    def _delayed_val(self, hist: deque, target_t: float) -> float:
        for ts, val in reversed(hist):
            if ts <= target_t + 1e-6:
                return val
        return hist[0][1]

    def update(self, now: float, books: dict) -> Tuple[Dict[str, float], Dict[str, List[float]], Dict[str, float]]:
        dt = 0.20 if self.last_t is None else max(0.05, min(1.0, now - self.last_t))
        self.last_t = now

        hmids: Dict[str, float] = {}
        micro_shift: Dict[str, float] = {}
        for s, (bids, asks) in books.items():
            if not bids or not asks:
                continue
            hm, _, _ = infer_house_mid(s, bids, asks)
            hmids[s] = hm
            bb, bv = bids[0]
            ba, av = asks[0]
            bvc, avc = min(bv, 100), min(av, 100)
            micro_shift[s] = (bb * avc + ba * bvc) / max(1, bvc + avc) - 0.5 * (bb + ba)

        # Update leader history & EMAs of HouseMid
        ema_hist_key = {0.50: "ema05", 1.00: "ema10"}
        for lead in LEADERS:
            if lead not in hmids:
                continue
            hm = hmids[lead]
            for hl in (0.50, 1.00):
                a = 1.0 - math.exp(-dt * math.log(2.0) / hl)
                prev = self.lead_ema[lead].get(hl, hm)
                self.lead_ema[lead][hl] = (1.0 - a) * prev + a * hm
            self.lead_hist[lead].append((now, hm, self.lead_ema[lead][0.50], self.lead_ema[lead][1.00]))

        f_star: Dict[str, float] = {}
        f_paths: Dict[str, List[float]] = {}

        for s, hm in hmids.items():
            if s in self.SPECS:
                lead, beta, lag_s, hl_s = self.SPECS[s]
                hist = self.lead_hist[lead]
                if hist:
                    # Find lagged EMA value at now - lag_s
                    idx_col = 2 if hl_s == 0.50 else 3
                    lag_ema = hist[0][idx_col]
                    for rec in reversed(hist):
                        if rec[0] <= now - lag_s + 1e-6:
                            lag_ema = rec[idx_col]
                            break
                    raw_alpha = hm - beta * lag_ema
                    if s not in self.alpha:
                        self.alpha[s] = raw_alpha
                    else:
                        # Fast clean lock-in first few steps, then steady 0.03 tracking
                        self.alpha[s] = 0.97 * self.alpha[s] + 0.03 * raw_alpha

                    alp = self.alpha[s]
                    cur_lead = hmids[lead]
                    target = alp + beta * cur_lead

                    # Build forward trajectory over next 2.4s in 0.4s increments!
                    path = []
                    if lag_s >= 2.0:
                        # For SNDK (lag=2.4s), future HouseMid at t + tau comes from NVDA at t - 2.4s + tau!
                        sim_ema = lag_ema
                        a_step = 1.0 - math.exp(-0.40 * math.log(2.0) / hl_s)
                        for tau in (0.4, 0.8, 1.2, 1.6, 2.0, 2.4):
                            src_t = now - lag_s + tau
                            lead_at_src = cur_lead
                            for rec in reversed(hist):
                                if rec[0] <= src_t + 1e-6:
                                    lead_at_src = rec[1]
                                    break
                            sim_ema = (1.0 - a_step) * sim_ema + a_step * lead_at_src
                            path.append(alp + beta * sim_ema)
                    else:
                        # Exponential convergence path from current hm toward target
                        cur_est = alp + beta * lag_ema
                        for tau in (0.4, 0.8, 1.2, 1.8, 2.4):
                            w = 1.0 - math.exp(-max(0.0, tau - lag_s) * math.log(2.0) / hl_s)
                            path.append(cur_est + w * (target - cur_est))

                    f_star[s] = target + 0.15 * micro_shift.get(s, 0.0)
                    f_paths[s] = path
                else:
                    f_star[s] = hm
                    f_paths[s] = [hm]
            else:
                f_star[s] = hm + 0.20 * micro_shift.get(s, 0.0)
                f_paths[s] = [f_star[s]]

        return f_star, f_paths, hmids


# ===========================================================================
# STRATEGY S4: StateSpaceTrajectoryArb
# ===========================================================================

class StateSpaceTrajectoryArb(QueueSmartExecMixin):
    """S4: Exact Deconvolved State-Space Follower Arbitrageur + Queue-Smart Maker."""

    TARGETS = ("SNDK", "NBIS", "CRWV")
    OPEN_HURDLE = 0.045    # Sweep to open inventory when edge >= 0.45 ticks
    COVER_HURDLE = 0.015   # Sweep to cover adverse/converged inventory when edge >= 0.15 ticks
    INV_SKEW = 0.0008      # Mild skew so full +-100 lot conviction is used on real moves

    def __init__(self, ex, folder=None):
        self._init_exec(ex)
        self.model = ExactStateSpaceModel()

    def step(self):
        books, mines = {}, {}
        for s in self.insts:
            b, a, m = self._clean_book(s)
            books[s] = (b, a)
            mines[s] = m

        f_star, f_paths, _ = self.model.update(self._now(), books)
        positions = self.ex.get_positions()

        # Pass 1: Immediately pull any resting quote that became toxic + execute high-EV IOC sweeps
        for s in self.TARGETS:
            if s not in f_star:
                continue
            bids, asks = books[s]
            if not bids or not asks:
                continue
            mine = mines[s]
            pos = int(positions.get(s, 0))
            fv = f_star[s]
            # For SNDK, blend 1.2s trajectory & 2.4s terminal so we don't jump against an immediate dip
            if s == "SNDK" and len(f_paths[s]) >= 3:
                fv = 0.35 * f_paths[s][2] + 0.65 * fv

            self._pull_toxic_orders(s, mine, fv, safe_margin=0.015)

            res_px = fv - self.INV_SKEW * pos
            bb, ba = bids[0][0], asks[0][0]

            # Step 1a: Cover opposing position at low hurdle
            if pos < 0 and fv - ba >= self.COVER_HURDLE:
                lim = floor_tick(fv - self.COVER_HURDLE)
                sent = self._sweep_ioc(s, "bid", lim, asks, pos, mine, max_target_pos=0)
                pos += sent
            elif pos > 0 and bb - fv >= self.COVER_HURDLE:
                lim = ceil_tick(fv + self.COVER_HURDLE)
                sent = self._sweep_ioc(s, "ask", lim, bids, pos, mine, max_target_pos=0)
                pos -= sent

            # Step 1b: Open / add in direction of strong edge up to +-100 lots!
            res_px = fv - self.INV_SKEW * pos
            if res_px - ba >= self.OPEN_HURDLE and pos < POS_LIMIT:
                lim = floor_tick(res_px - self.OPEN_HURDLE)
                sent = self._sweep_ioc(s, "bid", lim, asks, pos, mine, max_target_pos=POS_LIMIT)
                pos += sent
            elif bb - res_px >= self.OPEN_HURDLE and pos > -POS_LIMIT:
                lim = ceil_tick(res_px + self.OPEN_HURDLE)
                sent = self._sweep_ioc(s, "ask", lim, bids, pos, mine, max_target_pos=POS_LIMIT)
                pos -= sent
            positions[s] = pos

        # Pass 2: Queue-preserving passive quoting around reservation price (up to +-100 lots!)
        for s in self.TARGETS:
            if s not in f_star or self._budget(reserve=3) <= 0:
                continue
            bids, asks = books[s]
            if not bids or not asks:
                continue
            mine = mines[s]
            pos = int(positions.get(s, 0))
            fv = f_star[s]
            if s == "SNDK" and len(f_paths[s]) >= 3:
                fv = 0.35 * f_paths[s][2] + 0.65 * fv

            res_px = fv - self.INV_SKEW * pos
            bb, ba = bids[0][0], asks[0][0]

            bid_px = min(r1(ba - TICK), floor_tick(res_px - 0.04))
            ask_px = max(r1(bb + TICK), ceil_tick(res_px + 0.04))

            b_size = min(80, POS_LIMIT - pos) if pos < POS_LIMIT else 0
            a_size = min(80, POS_LIMIT + pos) if pos > -POS_LIMIT else 0
            want_b = (bid_px, b_size) if b_size > 0 else None
            want_a = (ask_px, a_size) if a_size > 0 else None

            self._sync_passive(s, pos, mine, want_b, want_a, min_keep_vol=12, hyst_ticks=0, reserve=3)


# ===========================================================================
# STRATEGY S5: DelayWaveRider (Forward Path-Extremum Wave Rider)
# ===========================================================================

class DelayWaveRider(QueueSmartExecMixin):
    """S5: Uses the full forward delay trajectory {F(t+0.4s)..F(t+2.4s)} to ride intermediate
    whipsaw waves on SNDK (2.4s NVDA buffer) + exponential trajectories on NBIS & CRWV,
    placing passive exits at the predicted forward wave extremum!"""

    TARGETS = ("SNDK", "NBIS", "CRWV")

    def __init__(self, ex, folder=None):
        self._init_exec(ex)
        self.model = ExactStateSpaceModel()

    def step(self):
        books, mines = {}, {}
        for s in self.insts:
            b, a, m = self._clean_book(s)
            books[s] = (b, a)
            mines[s] = m

        f_star, f_paths, _ = self.model.update(self._now(), books)
        positions = self.ex.get_positions()

        for s in self.TARGETS:
            if s not in f_star:
                continue
            bids, asks = books[s]
            if not bids or not asks:
                continue
            mine = mines[s]
            pos = int(positions.get(s, 0))
            path = f_paths[s]
            f_near = path[1] if len(path) >= 2 else f_star[s]   # +0.8s fair value
            f_end = f_star[s]                                   # +2.4s terminal fair value
            f_max = max(path + [f_end])
            f_min = min(path + [f_end])

            # Entry fair value requires near-horizon & terminal alignment on SNDK (avoids entering into a dip!)
            if s == "SNDK":
                bmid = 0.5 * (bids[0][0] + asks[0][0])
                if (f_near - bmid) * (f_end - bmid) < 0:
                    # Whipsaw in progress! Trade the near wave first!
                    f_active = f_near
                else:
                    f_active = 0.25 * f_near + 0.75 * f_end
            else:
                f_active = f_end

            self._pull_toxic_orders(s, mine, f_active, safe_margin=0.01)

            bb, ba = bids[0][0], asks[0][0]
            skew = 0.0007 * pos
            res = f_active - skew

            # Aggressive cover & sweep
            if pos < 0 and f_active - ba >= 0.01:
                pos += self._sweep_ioc(s, "bid", floor_tick(f_active - 0.01), asks, pos, mine, max_target_pos=0)
            elif pos > 0 and bb - f_active >= 0.01:
                pos -= self._sweep_ioc(s, "ask", ceil_tick(f_active + 0.01), bids, pos, mine, max_target_pos=0)

            res = f_active - 0.0007 * pos
            if res - ba >= 0.04 and pos < POS_LIMIT:
                pos += self._sweep_ioc(s, "bid", floor_tick(res - 0.04), asks, pos, mine, max_target_pos=POS_LIMIT)
            elif bb - res >= 0.04 and pos > -POS_LIMIT:
                pos -= self._sweep_ioc(s, "ask", ceil_tick(res + 0.04), bids, pos, mine, max_target_pos=POS_LIMIT)
            positions[s] = pos

            # Place passive exit at wave extremum (f_max for longs, f_min for shorts) to capture full swing!
            if self._budget(reserve=3) > 0:
                res = f_active - 0.0007 * pos
                bid_entry = min(r1(ba - TICK), floor_tick(res - 0.035))
                ask_entry = max(r1(bb + TICK), ceil_tick(res + 0.035))
                if pos > 0:
                    ask_exit = max(r1(bb + TICK), ceil_tick(max(res + 0.03, f_max - 0.01)))
                    ask_px = ask_exit
                    bid_px = min(bid_entry, r1(ask_px - TICK))
                elif pos < 0:
                    bid_exit = min(r1(ba - TICK), floor_tick(min(res - 0.03, f_min + 0.01)))
                    bid_px = bid_exit
                    ask_px = max(ask_entry, r1(bid_px + TICK))
                else:
                    bid_px, ask_px = bid_entry, ask_entry

                b_sz = min(85, POS_LIMIT - pos) if pos < POS_LIMIT else 0
                a_sz = min(85, POS_LIMIT + pos) if pos > -POS_LIMIT else 0
                self._sync_passive(
                    s, pos, mine,
                    (bid_px, b_sz) if b_sz > 0 else None,
                    (ask_px, a_sz) if a_sz > 0 else None,
                    min_keep_vol=15, hyst_ticks=0, reserve=3,
                )


# ===========================================================================
# STRATEGY S6: StructuralGLFTMaker (All-8-Stock GLFT Market Maker + Mispricing Taker)
# ===========================================================================

class StructuralGLFTMaker(QueueSmartExecMixin):
    """S6: Trades ALL 8 STOCKS (AMD, AVGO, CRWV, MU, NBIS, NVDA, SNDK, VRT) using
    Guéant-Lehalle-Fernandez-Tapia non-linear inventory skew around structural HouseMid +
    state-space cross-asset targets, sniping mispriced student orders on all 8 stocks and
    quoting inside wide house walls whenever BBO queue is thin!"""

    ALL_ORDER = ("SNDK", "NBIS", "CRWV", "AVGO", "AMD", "NVDA", "MU", "VRT")

    def __init__(self, ex, folder=None):
        self._init_exec(ex)
        self.model = ExactStateSpaceModel()

    def _glft_skew(self, inst: str, pos: int) -> float:
        if pos == 0:
            return 0.0
        # Higher risk aversion on unhedged random-walk leaders; gentle skew on high-R2 followers
        gamma = 0.0006 if inst in FOLLOWERS else 0.0022
        return gamma * math.copysign(abs(pos) ** 1.20, pos)

    def step(self):
        books, mines = {}, {}
        for s in self.insts:
            b, a, m = self._clean_book(s)
            books[s] = (b, a)
            mines[s] = m

        f_star, f_paths, hmids = self.model.update(self._now(), books)
        positions = self.ex.get_positions()

        # Pass 1: Toxic cancel + Opportunistic mispricing sweeps across ALL 8 STOCKS!
        for s in self.ALL_ORDER:
            if s not in f_star:
                continue
            bids, asks = books[s]
            if not bids or not asks:
                continue
            mine = mines[s]
            pos = int(positions.get(s, 0))
            fv = f_star[s]
            if s == "SNDK" and len(f_paths[s]) >= 3:
                fv = 0.30 * f_paths[s][2] + 0.70 * fv

            self._pull_toxic_orders(s, mine, fv, safe_margin=0.015 if s in FOLLOWERS else 0.03)

            bb, ba = bids[0][0], asks[0][0]
            is_fol = s in FOLLOWERS
            max_cap = POS_LIMIT if is_fol else 60
            open_hurdle = 0.045 if is_fol else 0.085
            cover_hurdle = 0.015 if is_fol else 0.025

            if pos < 0 and fv - ba >= cover_hurdle:
                pos += self._sweep_ioc(s, "bid", floor_tick(fv - cover_hurdle), asks, pos, mine, max_target_pos=0)
            elif pos > 0 and bb - fv >= cover_hurdle:
                pos -= self._sweep_ioc(s, "ask", ceil_tick(fv + cover_hurdle), bids, pos, mine, max_target_pos=0)

            res = fv - self._glft_skew(s, pos)
            if res - ba >= open_hurdle and pos < max_cap:
                pos += self._sweep_ioc(s, "bid", floor_tick(res - open_hurdle), asks, pos, mine, max_target_pos=max_cap)
            elif bb - res >= open_hurdle and pos > -max_cap:
                pos -= self._sweep_ioc(s, "ask", ceil_tick(res + open_hurdle), bids, pos, mine, max_target_pos=max_cap)
            positions[s] = pos

        # Pass 2: Continuous GLFT quoting across followers + wide-house-spread leaders (AVGO, AMD, NVDA)
        for s in self.ALL_ORDER:
            if s not in f_star or self._budget(reserve=3) <= 0:
                continue
            bids, asks = books[s]
            if not bids or not asks:
                continue
            mine = mines[s]
            pos = int(positions.get(s, 0))
            fv = f_star[s]
            if s == "SNDK" and len(f_paths[s]) >= 3:
                fv = 0.30 * f_paths[s][2] + 0.70 * fv

            res = fv - self._glft_skew(s, pos)
            bb, bv = bids[0]
            ba, av = asks[0]
            is_fol = s in FOLLOWERS

            if is_fol:
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
            else:
                # On leaders: quote passive exit at HouseMid +- 0.03 whenever holding inventory;
                # only enter passively if stepping strictly inside BBO (queue == 0) with >= 0.09 edge to HouseMid!
                want_b = want_a = None
                bid_px = min(r1(ba - TICK), floor_tick(res - 0.09))
                ask_px = max(r1(bb + TICK), ceil_tick(res + 0.09))
                if pos < 50 and bid_px > bb + EPS:
                    want_b = (bid_px, min(35, 50 - pos))
                if pos > -50 and ask_px < ba - EPS:
                    want_a = (ask_px, min(35, 50 + pos))
                if pos > 0:
                    exit_a = max(r1(bb + TICK), ceil_tick(res + 0.02))
                    want_a = (exit_a, pos)
                elif pos < 0:
                    exit_b = min(r1(ba - TICK), floor_tick(res - 0.02))
                    want_b = (exit_b, -pos)
                self._sync_passive(s, pos, mine, want_b, want_a, min_keep_vol=10, hyst_ticks=0, reserve=4)


# ===========================================================================
# STRATEGY S7: KalmanInnovationEngine (Zero-Hardcoded-Beta 2D Online Kalman + Trade Flow)
# ===========================================================================

class Online2DKalmanPair:
    """Learns [alpha_t, beta_t] completely online from scratch via 2-state Kalman Filter!"""

    def __init__(self, q_alpha: float = 1e-4, q_beta: float = 1e-6, r_obs: float = 0.01):
        self.theta = np.array([0.0, 0.5], dtype=float)
        self.P = np.diag([1000.0, 10.0])
        self.Q = np.diag([q_alpha, q_beta])
        self.R = r_obs
        self.n_obs = 0

    def update(self, x_lagged: float, y_obs: float) -> Tuple[float, float]:
        if self.n_obs == 0:
            self.theta = np.array([y_obs - 0.5 * x_lagged, 0.5], dtype=float)
            self.n_obs = 1
            return float(self.theta[0]), float(self.theta[1])
        P_pred = self.P + self.Q
        H = np.array([1.0, x_lagged], dtype=float)
        err = y_obs - float(H @ self.theta)
        S = float(H @ P_pred @ H) + self.R
        K = (P_pred @ H) / max(1e-9, S)
        self.theta = self.theta + K * err
        self.P = P_pred - np.outer(K, H) @ P_pred
        self.n_obs += 1
        return float(self.theta[0]), float(self.theta[1])


class KalmanInnovationEngine(QueueSmartExecMixin):
    """S7: Zero-Hardcoded-Beta Online 2D Kalman Filter + Signed Trade-Flow Impulse Engine.
    Learns both intercept alpha_t and hedge slope beta_t live from scratch (warmup = 15s),
    fused with live public TradeTick aggressor-flow bursts!"""

    PAIR_DEFS = {
        "SNDK": ("NVDA", 2.40, 0.50),
        "NBIS": ("AVGO", 0.40, 1.00),
        "CRWV": ("MU",   0.00, 1.00),
    }

    def __init__(self, ex, folder=None):
        self._init_exec(ex)
        self.kf = {s: Online2DKalmanPair() for s in self.PAIR_DEFS}
        self.lead_hist = {s: deque(maxlen=60) for s in LEADERS}
        self.lead_ema = {s: {} for s in LEADERS}
        self.flow_ema = {s: 0.0 for s in ["AMD", "AVGO", "CRWV", "MU", "NBIS", "NVDA", "SNDK", "VRT"]}
        self.last_t: Optional[float] = None

    def step(self):
        now = self._now()
        dt = 0.20 if self.last_t is None else max(0.05, min(1.0, now - self.last_t))
        self.last_t = now

        books, mines, hmids = {}, {}, {}
        for s in self.insts:
            b, a, m = self._clean_book(s)
            books[s] = (b, a)
            mines[s] = m
            if b and a:
                hmids[s], _, _ = infer_house_mid(s, b, a)
            # Ingest live public trade ticks for signed order-flow alpha!
            signed_v = 0.0
            if hasattr(self.ex, "poll_new_trade_ticks"):
                for tr in self.ex.poll_new_trade_ticks(s):
                    ag = getattr(tr, "aggressor_side", "")
                    if ag == "bid":
                        signed_v += float(tr.volume)
                    elif ag == "ask":
                        signed_v -= float(tr.volume)
            decay = math.exp(-dt / 1.5)
            self.flow_ema[s] = decay * self.flow_ema.get(s, 0.0) + signed_v

        for lead in LEADERS:
            if lead not in hmids:
                continue
            hm = hmids[lead]
            for hl in (0.50, 1.00):
                a = 1.0 - math.exp(-dt * math.log(2.0) / hl)
                self.lead_ema[lead][hl] = (1.0 - a) * self.lead_ema[lead].get(hl, hm) + a * hm
            self.lead_hist[lead].append((now, hm, self.lead_ema[lead][0.50], self.lead_ema[lead][1.00]))

        positions = self.ex.get_positions()

        for s, (lead, lag_s, hl_s) in self.PAIR_DEFS.items():
            if s not in hmids or lead not in hmids:
                continue
            hist = self.lead_hist[lead]
            if not hist:
                continue
            col = 2 if hl_s == 0.50 else 3
            lag_x = hist[0][col]
            for rec in reversed(hist):
                if rec[0] <= now - lag_s + 1e-6:
                    lag_x = rec[col]
                    break

            alp, bet = self.kf[s].update(lag_x, hmids[s])
            # Require 40 ticks (~8 seconds) for 2D slope beta_t to lock in cleanly
            if self.kf[s].n_obs < 40 or not (0.15 <= bet <= 2.50):
                continue

            # Combine Kalman asymptotic prediction + live signed order-flow impulse
            flow_imp = max(-0.08, min(0.08, 0.0006 * self.flow_ema.get(s, 0.0)))
            fv = alp + bet * hmids[lead] + flow_imp

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
