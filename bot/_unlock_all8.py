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

class UnlockedAll8:
    FOL_HALF_SP = {"SNDK": 0.038, "NBIS": 0.042, "CRWV": 0.048}
    FOL_SKEW = {"SNDK": 0.00045, "NBIS": 0.00050, "CRWV": 0.00060}
    LEAD_HALF_SP = {"AVGO": 0.12, "AMD": 0.12, "NVDA": 0.09, "MU": 0.045, "VRT": 0.045}
    LEAD_SKEW = {"AVGO": 0.0020, "AMD": 0.0022, "NVDA": 0.0018, "MU": 0.0012, "VRT": 0.0012}
    LEAD_CAP = 90
    MAX_REQ = 24
    PIN_WALL_FRONT = False

    PAIRS = {
        "SNDK": ("NVDA", 0.4240, 0.0012),
        "NBIS": ("AVGO", 0.4055, 0.0012),
        "CRWV": ("MU",   1.1100, 0.0012),
    }

    def __init__(self, ex, folder=None):
        self.ex = ex
        self.insts = list(getattr(ex, "instruments", [])) or sorted(ex.get_tradable_instruments().keys())
        self.req_ts = deque()
        self.alpha = {}
        self.obs = {}
        self.mid_hist = {s: deque(maxlen=30) for s in self.insts}

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
        books, bbo_mids, intact_hm, lead_px, micro = {}, {}, {}, {}, {}
        for s in self.insts:
            b, a, _ = self.clean_book(s)
            books[s] = (b, a)
            if not b or not a:
                continue
            bb, bv = b[0]
            ba, av = a[0]
            mid = 0.5 * (bb + ba)
            bbo_mids[s] = mid
            self.mid_hist[s].append((now, mid))
            bvc, avc = min(bv, 110), min(av, 110)
            micro[s] = (bb * avc + ba * bvc) / max(1, bvc + avc) - mid
            if s in LEADERS:
                info = strict_leader_hm(b, a)
                if info is not None:
                    intact_hm[s] = info
                    lead_px[s] = info[0]
                else:
                    lead_px[s] = mid

        positions = self.ex.get_positions()

        # 1) Followers
        for s in ("NBIS", "CRWV", "SNDK"):
            lead, beta, rate = self.PAIRS[s]
            if s not in bbo_mids or lead not in lead_px:
                continue
            raw_a = bbo_mids[s] - beta * lead_px[lead]
            n = self.obs.get(s, 0) + 1
            self.obs[s] = n
            if n == 1:
                self.alpha[s] = raw_a
            elif n <= 20:
                self.alpha[s] = ((n - 1) * self.alpha[s] + raw_a) / n
            else:
                self.alpha[s] = (1.0 - rate) * self.alpha[s] + rate * raw_a

            fv = self.alpha[s] + beta * lead_px[lead] + 0.25 * micro[s]
            if s in ("CRWV", "NBIS"):
                h = self.mid_hist[s]
                m_1s = bbo_mids[s]
                for ts, mp in reversed(h):
                    if now - ts >= 0.8:
                        m_1s = mp
                        break
                fv += 0.28 * (bbo_mids[s] - m_1s)

            pos = int(positions.get(s, 0))
            res = fv - self.FOL_SKEW[s] * pos
            hsp = self.FOL_HALF_SP[s]
            bp = floor_tick(res - hsp)
            ap = ceil_tick(res + hsp)
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

        # 2) Leaders: NO `bp > bb` blockade! Rest continuously inside LivelyLasagnas's 3000-lot walls!
        for s in ("AVGO", "AMD", "NVDA", "VRT", "MU"):
            if self.budget(reserve=3) <= 0 or s not in intact_hm:
                continue
            hm, wb, wa = intact_hm[s]
            pos = int(self.ex.get_positions().get(s, 0))
            fv = hm + 0.20 * micro[s]
            res = fv - self.LEAD_SKEW[s] * pos
            if self.PIN_WALL_FRONT:
                # Rest 1 tick in front of 3000-lot wall for entry; skew closer to HouseMid for exit!
                bp = r1(wb + TICK)
                ap = r1(wa - TICK)
            else:
                hsp = self.LEAD_HALF_SP[s]
                bp = max(r1(wb + TICK), min(r1(wa - TICK), floor_tick(res - hsp)))
                ap = min(r1(wa - TICK), max(r1(wb + TICK), ceil_tick(res + hsp)))

            if pos > 0:
                ap = max(r1(wb + TICK), min(ap, ceil_tick(res + 0.02)))
            elif pos < 0:
                bp = min(r1(wa - TICK), max(bp, floor_tick(res - 0.02)))

            if bp >= ap - EPS:
                if pos >= 0:
                    bp = r1(ap - TICK)
                else:
                    ap = r1(bp + TICK)

            want_b = (bp, min(self.LEAD_CAP, self.LEAD_CAP - pos)) if (pos < self.LEAD_CAP and bp > wb + EPS) else None
            want_a = (ap, min(self.LEAD_CAP, self.LEAD_CAP + pos)) if (pos > -self.LEAD_CAP and ap < wa - EPS) else None
            if pos > 0 and want_a is None:
                want_a = (max(r1(wb + TICK), ceil_tick(res + 0.02)), pos)
            elif pos < 0 and want_b is None:
                want_b = (min(r1(wa - TICK), floor_tick(res - 0.02)), -pos)

            self.place_unified_quotes(s, want_b, want_a, reserve=3)

class V1_UnlockedRes(UnlockedAll8):
    PIN_WALL_FRONT = False

class V2_PinnedWallFront(UnlockedAll8):
    PIN_WALL_FRONT = True

class V3_WiderWallFront(UnlockedAll8):
    LEAD_HALF_SP = {"AVGO": 0.16, "AMD": 0.15, "NVDA": 0.11, "MU": 0.055, "VRT": 0.055}
