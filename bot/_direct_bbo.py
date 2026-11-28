from __future__ import annotations
import math, time
from collections import deque
from typing import Dict, List, Optional, Tuple

TICK = 0.1
EPS = 1e-9
POS_LIMIT = 100
MAX_OPEN_VOL = 200
LEADERS = {"AMD", "AVGO", "MU", "NVDA", "VRT"}
FOLLOWERS = {"CRWV", "NBIS", "SNDK"}

def r1(x: float) -> float:
    return round(x + 0.0, 1)

def floor_tick(x: float) -> float:
    return round(math.floor(x / TICK + 1e-9) * TICK, 1)

def ceil_tick(x: float) -> float:
    return round(math.ceil(x / TICK - 1e-9) * TICK, 1)

def strict_leader_hm(bids, asks):
    wb = next((p for p, v in bids if v >= 2400), None)
    wa = next((p for p, v in asks if v >= 2400), None)
    if wb is not None and wa is not None and wa > wb + 0.15:
        return 0.5 * (wb + wa), wb, wa
    wb2 = next((p for p, v in bids if v >= 1700), None)
    wa2 = next((p for p, v in asks if v >= 1700), None)
    if wb2 is not None and wa2 is not None and wa2 > wb2 + 0.35:
        return 0.5 * (wb2 + wa2), r1(wb2 + 0.1), r1(wa2 - 0.1)
    return None

class DirectBBODriftBase:
    HORIZON_SCALE = 1.35    # Scale 5s forecast to asymptotic ~8-10s move
    HALF_SP = 0.038
    SKEW = 0.00050
    LEAD_SNIPE_EDGE = 0.065
    LEAD_CAP = 90
    MAX_REQ = 24

    # Weights learned STRICTLY on Train Chunks 0..11 (validated OOS on Chunks 12..23 with R2=+0.64/+0.28/+0.27!)
    # Features: [gap_slow, gap_fast, b*dL_0.5s, b*dL_1.5s, b*dL_3.0s, dF_0.5s, dF_1.5s, micro]
    MODEL_5S = {
        "SNDK": ("NVDA", 0.424, (0.480, 0.290, -0.022, 0.015, 0.205, -0.069, 0.027, 0.100)),
        "NBIS": ("AVGO", 0.405, (0.090, 0.395,  0.194, 0.137, 0.097, -0.041, 0.046, 0.232)),
        "CRWV": ("MU",   1.110, (0.010, 0.235,  0.303, 0.185, 0.140, -0.040, 0.053, 0.385)),
    }
    MODEL_2S = {
        "SNDK": ("NVDA", 0.424, (0.417, 0.254, -0.350, -0.208, 0.245, -0.086, 0.012, 0.054)),
        "NBIS": ("AVGO", 0.405, (0.055, 0.278,  0.167,  0.117, 0.100, -0.102, 0.029, 0.177)),
        "CRWV": ("MU",   1.110, (0.038, 0.137,  0.235,  0.158, 0.109, -0.038, 0.022, 0.306)),
    }
    BLEND_2S = 0.0

    def __init__(self, ex, folder=None):
        self.ex = ex
        self.insts = list(getattr(ex, "instruments", [])) or sorted(ex.get_tradable_instruments().keys())
        self.req_ts = deque()
        self.a_slow = {}
        self.a_fast = {}
        self.obs = {}
        self.hist = {s: deque(maxlen=40) for s in self.insts}

    def now(self) -> float:
        clk = getattr(self.ex, "clock", None)
        return clk.time() if clk is not None else time.time()

    def budget(self, reserve: int = 0) -> int:
        t = self.now()
        while self.req_ts and t - self.req_ts[0] >= 1.0 - 1e-9:
            self.req_ts.popleft()
        return max(0, self.MAX_REQ - reserve - len(self.req_ts))

    def spend(self, reserve: int = 0) -> bool:
        if self.budget(reserve) <= 0:
            return False
        self.req_ts.append(self.now())
        return True

    def clean_book(self, inst: str):
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

    @staticmethod
    def _ago(dq: deque, now: float, dt_target: float) -> float:
        for ts, val in reversed(dq):
            if now - ts >= dt_target - 1e-6:
                return val
        return dq[0][1]

    def place_unified_quotes(self, inst: str, want_b: Optional[Tuple[float, int]], want_a: Optional[Tuple[float, int]], reserve: int = 0):
        mine = self.ex.get_outstanding_orders(inst)
        pos = int(self.ex.get_positions().get(inst, 0))

        if want_b and want_a and want_b[0] >= want_a[0] - EPS:
            if pos >= 0:
                want_b = (r1(want_a[0] - TICK), want_b[1])
            else:
                want_a = (r1(want_b[0] + TICK), want_a[1])

        cur_b = [o for o in mine.values() if o.side == "bid"]
        cur_a = [o for o in mine.values() if o.side == "ask"]

        b_ok = (want_b is None and not cur_b) or (
            want_b is not None and len(cur_b) == 1 and abs(cur_b[0].price - want_b[0]) < EPS
            and max(10, int(want_b[1] * 0.35)) <= cur_b[0].volume <= want_b[1] + 25
            and (want_a is None or cur_b[0].price < want_a[0] - EPS)
        )
        a_ok = (want_a is None and not cur_a) or (
            want_a is not None and len(cur_a) == 1 and abs(cur_a[0].price - want_a[0]) < EPS
            and max(10, int(want_a[1] * 0.35)) <= cur_a[0].volume <= want_a[1] + 25
            and (want_b is None or cur_a[0].price > want_b[0] + EPS)
        )
        if b_ok and a_ok:
            return

        cross_conflict = (
            (not b_ok and want_b and any(o.price <= want_b[0] + EPS for o in cur_a)) or
            (not a_ok and want_a and any(o.price >= want_a[0] - EPS for o in cur_b))
        )
        if cross_conflict or (not b_ok and not a_ok and len(mine) >= 2):
            if not self.spend(reserve=0 if cross_conflict else reserve):
                return
            self.ex.delete_orders(inst)
            cur_b, cur_a = [], []
            b_ok = a_ok = False

        if not b_ok:
            mine = self.ex.get_outstanding_orders(inst)
            for o in [x for x in mine.values() if x.side == "bid"]:
                if not self.spend(reserve):
                    return
                self.ex.delete_order(inst, order_id=o.order_id)
            if want_b:
                pos = int(self.ex.get_positions().get(inst, 0))
                mine = self.ex.get_outstanding_orders(inst)
                if not any(o.side == "ask" and o.price <= want_b[0] + EPS for o in mine.values()):
                    open_b = sum(o.volume for o in mine.values() if o.side == "bid")
                    open_tot = sum(o.volume for o in mine.values())
                    v = min(want_b[1], POS_LIMIT - pos - open_b, MAX_OPEN_VOL - open_tot)
                    if v > 0 and self.spend(reserve):
                        self.ex.insert_order(inst, price=want_b[0], volume=int(v), side="bid", order_type="limit")

        if not a_ok:
            mine = self.ex.get_outstanding_orders(inst)
            for o in [x for x in mine.values() if x.side == "ask"]:
                if not self.spend(reserve):
                    return
                self.ex.delete_order(inst, order_id=o.order_id)
            if want_a:
                pos = int(self.ex.get_positions().get(inst, 0))
                mine = self.ex.get_outstanding_orders(inst)
                if not any(o.side == "bid" and o.price >= want_a[0] - EPS for o in mine.values()):
                    open_a = sum(o.volume for o in mine.values() if o.side == "ask")
                    open_tot = sum(o.volume for o in mine.values())
                    v = min(want_a[1], POS_LIMIT + pos - open_a, MAX_OPEN_VOL - open_tot)
                    if v > 0 and self.spend(reserve):
                        self.ex.insert_order(inst, price=want_a[0], volume=int(v), side="ask", order_type="limit")

    def step(self):
        now = self.now()
        books, bbo_mids, intact_hm, lead_ref, micro = {}, {}, {}, {}, {}
        for s in self.insts:
            b, a, _ = self.clean_book(s)
            books[s] = (b, a)
            if not b or not a:
                continue
            bb, bv = b[0]
            ba, av = a[0]
            mid = 0.5 * (bb + ba)
            bbo_mids[s] = mid
            bvc, avc = min(bv, 100), min(av, 100)
            micro[s] = (bb * avc + ba * bvc) / max(1, bvc + avc) - mid
            if s in LEADERS:
                info = strict_leader_hm(b, a)
                if info is not None:
                    intact_hm[s] = info
                    lead_ref[s] = info[0]
                else:
                    lead_ref[s] = mid
                self.hist[s].append((now, lead_ref[s]))
            else:
                self.hist[s].append((now, mid))

        positions = self.ex.get_positions()

        # 1) Followers: Direct High-R2 BBO Drift Model!
        for s in ("NBIS", "CRWV", "SNDK"):
            lead, beta, w5 = self.MODEL_5S[s]
            _, _, w2 = self.MODEL_2S[s]
            if s not in bbo_mids or lead not in lead_ref:
                continue
            mf = bbo_mids[s]
            ml = lead_ref[lead]
            raw_a = mf - beta * ml
            n = self.obs.get(s, 0) + 1
            self.obs[s] = n
            if n == 1:
                self.a_slow[s] = self.a_fast[s] = raw_a
            else:
                self.a_slow[s] = 0.9975 * self.a_slow[s] + 0.0025 * raw_a
                self.a_fast[s] = 0.9650 * self.a_fast[s] + 0.0350 * raw_a

            hl_dq = self.hist[lead]
            hf_dq = self.hist[s]
            feats = (
                (self.a_slow[s] + beta * ml) - mf,
                (self.a_fast[s] + beta * ml) - mf,
                beta * (ml - self._ago(hl_dq, now, 0.5)),
                beta * (ml - self._ago(hl_dq, now, 1.5)),
                beta * (ml - self._ago(hl_dq, now, 3.0)),
                mf - self._ago(hf_dq, now, 0.5),
                mf - self._ago(hf_dq, now, 1.5),
                micro[s],
            )
            d5 = sum(wi * fi for wi, fi in zip(w5, feats))
            d2 = sum(wi * fi for wi, fi in zip(w2, feats))
            pred_move = ((1.0 - self.BLEND_2S) * d5 + self.BLEND_2S * d2) * self.HORIZON_SCALE
            fv = mf + pred_move

            pos = int(positions.get(s, 0))
            res = fv - self.SKEW * pos
            bp = floor_tick(res - self.HALF_SP)
            ap = ceil_tick(res + self.HALF_SP)
            if bp >= ap - EPS:
                ap = r1(bp + TICK)

            bv = max(0, POS_LIMIT - pos) if bp <= fv - 0.025 else max(0, -pos)
            av = max(0, POS_LIMIT + pos) if ap >= fv + 0.025 else max(0, pos)
            if bv + av > MAX_OPEN_VOL:
                if pos > 0:
                    av = min(av, MAX_OPEN_VOL); bv = max(0, min(bv, MAX_OPEN_VOL - av))
                elif pos < 0:
                    bv = min(bv, MAX_OPEN_VOL); av = max(0, min(av, MAX_OPEN_VOL - bv))
                else:
                    bv = min(bv, 100); av = min(av, 100)

            self.place_unified_quotes(s, (bp, bv) if bv > 0 else None, (ap, av) if av > 0 else None, reserve=0)

        # 2) Leaders: PURE STUDENT-MISPRICING IOC SNIPER + FAST PASSIVE UNWIND (ZERO RESTING PASSIVE ENTRIES!)
        for s in ("AVGO", "AMD", "NVDA", "MU", "VRT"):
            if self.budget(reserve=3) <= 0 or s not in intact_hm:
                continue
            hm, wb, wa = intact_hm[s]
            bids, asks = books[s]
            bb, ba = bids[0][0], asks[0][0]
            pos = int(self.ex.get_positions().get(s, 0))
            fv = hm + 0.20 * micro[s]

            # Pure IOC Snipe when student BBO crosses HouseMid by >= LEAD_SNIPE_EDGE!
            if fv - ba >= self.LEAD_SNIPE_EDGE and pos < self.LEAD_CAP:
                lim = min(r1(wa - TICK), floor_tick(fv - self.LEAD_SNIPE_EDGE + 0.01))
                avail = sum(v for p, v in asks if p <= lim + EPS)
                if avail > 0:
                    mine = self.ex.get_outstanding_orders(s)
                    if mine and self.spend(reserve=2):
                        self.ex.delete_orders(s)
                    v = min(avail, self.LEAD_CAP - pos)
                    if v > 0 and self.spend(reserve=2):
                        self.ex.insert_order(s, price=lim, volume=int(v), side="bid", order_type="ioc")
                        pos = int(self.ex.get_positions().get(s, 0))
            elif bb - fv >= self.LEAD_SNIPE_EDGE and pos > -self.LEAD_CAP:
                lim = max(r1(wb + TICK), ceil_tick(fv + self.LEAD_SNIPE_EDGE - 0.01))
                avail = sum(v for p, v in bids if p >= lim - EPS)
                if avail > 0:
                    mine = self.ex.get_outstanding_orders(s)
                    if mine and self.spend(reserve=2):
                        self.ex.delete_orders(s)
                    v = min(avail, self.LEAD_CAP + pos)
                    if v > 0 and self.spend(reserve=2):
                        self.ex.insert_order(s, price=lim, volume=int(v), side="ask", order_type="ioc")
                        pos = int(self.ex.get_positions().get(s, 0))

            # Passive unwind ONLY (zero resting entry when pos == 0!)
            want_b = want_a = None
            res = fv - 0.0015 * pos
            if pos > 0:
                want_a = (max(r1(bb + TICK), ceil_tick(res + 0.01)), pos)
            elif pos < 0:
                want_b = (min(r1(ba - TICK), floor_tick(res - 0.01)), -pos)
            self.place_unified_quotes(s, want_b, want_a, reserve=3)

class D1_Scale10(DirectBBODriftBase):
    HORIZON_SCALE = 1.00; HALF_SP = 0.035; BLEND_2S = 0.30

class D2_Scale15(DirectBBODriftBase):
    HORIZON_SCALE = 1.50; HALF_SP = 0.038; BLEND_2S = 0.25

class D3_Scale20(DirectBBODriftBase):
    HORIZON_SCALE = 2.00; HALF_SP = 0.042; BLEND_2S = 0.20

class D4_Scale25(DirectBBODriftBase):
    HORIZON_SCALE = 2.50; HALF_SP = 0.045; BLEND_2S = 0.15
