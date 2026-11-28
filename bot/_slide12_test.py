from __future__ import annotations
import math, time
from collections import deque
from typing import Dict, List, Optional, Tuple

TICK = 0.1
EPS = 1e-9
POS_LIMIT = 100
MAX_OPEN_VOL = 190
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

class Slide12Test(object):
    HALF_SP = 0.055
    SKEW = 0.0006
    AR_WT = 0.28
    MICRO_WT = 0.25
    USE_LEADERS = True
    LEAD_HALF_SP = 0.085
    LEAD_CAP = 80
    MAX_REQ = 23

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

    def place_unified_quotes(self, inst: str, want_b: Optional[Tuple[float, int]], want_a: Optional[Tuple[float, int]], reserve: int = 1):
        """Optiver Slide-12 Unified Limit Quoting:
        Allows limit orders to cross mispriced opposite book levels (sweeping immediately AND resting
        remaining volume at queue=0 in 1 single request!), while strictly guaranteeing ZERO self-trades
        and obeying +-100 position & <=190 open-volume caps!"""
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
            and max(10, int(want_b[1] * 0.35)) <= cur_b[0].volume <= want_b[1] + 20
            and (want_a is None or cur_b[0].price < want_a[0] - EPS)
        )
        a_ok = (want_a is None and not cur_a) or (
            want_a is not None and len(cur_a) == 1 and abs(cur_a[0].price - want_a[0]) < EPS
            and max(10, int(want_a[1] * 0.35)) <= cur_a[0].volume <= want_a[1] + 20
            and (want_b is None or cur_a[0].price > want_b[0] + EPS)
        )
        if b_ok and a_ok:
            return

        # Self-trade prevention: if new bid crosses existing resting ask (or new ask crosses existing resting bid),
        # or both sides need replacement, cancel all in 1 request!
        cross_conflict = (
            (not b_ok and want_b and any(o.price <= want_b[0] + EPS for o in cur_a)) or
            (not a_ok and want_a and any(o.price >= want_a[0] - EPS for o in cur_b))
        )
        if cross_conflict or (not b_ok and not a_ok and len(mine) >= 2):
            if not self.spend(reserve=0 if cross_conflict else reserve):
                return
            self.ex.delete_orders(inst)
            mine = {}
            cur_b, cur_a = [], []
            b_ok = a_ok = False

        # Re-read pos before each insert because a crossing limit order immediately changes pos!
        if not b_ok:
            for o in cur_b:
                if not self.spend(reserve):
                    return
                self.ex.delete_order(inst, order_id=o.order_id)
                mine.pop(o.order_id, None)
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
            cur_a = [o for o in mine.values() if o.side == "ask"]
            for o in cur_a:
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

        # 1) Followers with unified Slide-12 crossing-limit execution!
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

            fv = self.alpha[s] + beta * lead_px[lead] + self.MICRO_WT * micro[s]
            if s in ("CRWV", "NBIS"):
                h = self.mid_hist[s]
                m_1s = bbo_mids[s]
                for ts, mp in reversed(h):
                    if now - ts >= 0.8:
                        m_1s = mp
                        break
                fv += self.AR_WT * (bbo_mids[s] - m_1s)

            pos = int(positions.get(s, 0))
            res = fv - self.SKEW * pos
            bp = floor_tick(res - self.HALF_SP)
            ap = ceil_tick(res + self.HALF_SP)
            if bp >= ap - EPS:
                ap = r1(bp + TICK)

            # Size up to +-100 (and unwind+flip up to 190 when edge >= 0.04!)
            bv = max(0, POS_LIMIT - pos) if bp <= fv - 0.035 else max(0, -pos)
            av = max(0, POS_LIMIT + pos) if ap >= fv + 0.035 else max(0, pos)
            if bv + av > MAX_OPEN_VOL:
                if pos > 0:
                    av = min(av, MAX_OPEN_VOL); bv = max(0, min(bv, MAX_OPEN_VOL - av))
                elif pos < 0:
                    bv = min(bv, MAX_OPEN_VOL); av = max(0, min(av, MAX_OPEN_VOL - bv))
                else:
                    bv = min(bv, 95); av = min(av, 95)

            self.place_unified_quotes(
                s,
                (bp, bv) if bv > 0 else None,
                (ap, av) if av > 0 else None,
                reserve=0,
            )

        # 2) Leaders when intact symmetric 3000-lot walls exist!
        if self.USE_LEADERS:
            for s in ("AVGO", "NVDA", "AMD", "MU", "VRT"):
                if self.budget(reserve=4) <= 0 or s not in intact_hm:
                    continue
                hm, wb, wa = intact_hm[s]
                bids, asks = books[s]
                bb, ba = bids[0][0], asks[0][0]
                pos = int(self.ex.get_positions().get(s, 0))
                fv = hm + 0.22 * micro[s]
                res = fv - 0.0015 * pos
                # Never cross into LivelyLasagnas's 3000-lot wall itself; DO cross mispriced student quotes!
                bp = min(r1(wa - TICK), floor_tick(res - self.LEAD_HALF_SP))
                ap = max(r1(wb + TICK), ceil_tick(res + self.LEAD_HALF_SP))
                want_b = want_a = None
                # Enter/sweep only if strictly better than wall AND either crosses ask or steps inside BBO (queue=0)!
                if pos < self.LEAD_CAP and bp > wb + EPS and bp > bb + EPS:
                    want_b = (bp, min(self.LEAD_CAP, self.LEAD_CAP - pos))
                if pos > -self.LEAD_CAP and ap < wa - EPS and ap < ba - EPS:
                    want_a = (ap, min(self.LEAD_CAP, self.LEAD_CAP + pos))
                if pos > 0:
                    exit_a = max(r1(wb + TICK), ceil_tick(res + 0.02))
                    want_a = (exit_a, pos) if want_a is None else (min(want_a[0], exit_a), max(pos, want_a[1]))
                elif pos < 0:
                    exit_b = min(r1(wa - TICK), floor_tick(res - 0.02))
                    want_b = (exit_b, -pos) if want_b is None else (max(want_b[0], exit_b), max(-pos, want_b[1]))
                self.place_unified_quotes(s, want_b, want_a, reserve=4)

class U1_Base(Slide12Test):
    HALF_SP = 0.055; SKEW = 0.0006; LEAD_HALF_SP = 0.085

class U2_Tighter(Slide12Test):
    HALF_SP = 0.040; SKEW = 0.0005; LEAD_HALF_SP = 0.075

class U3_Wider(Slide12Test):
    HALF_SP = 0.070; SKEW = 0.0007; LEAD_HALF_SP = 0.090

class U4_ZeroSkewFastFlip(Slide12Test):
    HALF_SP = 0.045; SKEW = 0.0003; LEAD_HALF_SP = 0.085; LEAD_CAP = 100
