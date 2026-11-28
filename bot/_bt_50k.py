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

class BreakthroughBase:
    HALF_SP = 0.032
    SKEW = 0.00045
    FLIP_EDGE = 0.015
    ALPHA_RATE = 0.0020
    AR_WT = 0.32
    MICRO_WT = 0.28
    FLOW_WT = 0.0006
    USE_LEADERS = True
    LEAD_ACTIVE = ("AVGO", "NVDA", "MU", "VRT", "AMD")
    LEAD_HALF_SP = 0.042
    LEAD_SKEW = 0.0015
    LEAD_CAP = 100
    MAX_REQ = 24

    PAIRS = {
        "SNDK": ("NVDA", 0.4225),
        "NBIS": ("AVGO", 0.4040),
        "CRWV": ("MU",   1.1050),
    }

    def __init__(self, ex, folder=None):
        self.ex = ex
        self.insts = list(getattr(ex, "instruments", [])) or sorted(ex.get_tradable_instruments().keys())
        self.req_ts = deque()
        self.alpha = {}
        self.obs = {}
        self.mid_hist = {s: deque(maxlen=40) for s in self.insts}
        self.lead_hist = {s: deque(maxlen=40) for s in LEADERS}
        self.flow_ema = {s: 0.0 for s in self.insts}
        self.last_t = None

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

    def ext_vol_at(self, levels: list, px: float) -> int:
        for p, v in levels:
            if abs(p - px) < EPS:
                return v
        return 0

    def place_quotes(self, inst: str, bids: list, asks: list, want_b: Optional[Tuple[float, int]], want_a: Optional[Tuple[float, int]], reserve: int = 0):
        mine = self.ex.get_outstanding_orders(inst)
        pos = int(self.ex.get_positions().get(inst, 0))

        if want_b and want_a and want_b[0] >= want_a[0] - EPS:
            if pos >= 0:
                want_b = (r1(want_a[0] - TICK), want_b[1])
            else:
                want_a = (r1(want_b[0] + TICK), want_a[1])

        cur_b = [o for o in mine.values() if o.side == "bid"]
        cur_a = [o for o in mine.values() if o.side == "ask"]

        # If external queue at want_b[0] is 0 (we are alone at BBO or crossing!), reload when < 75% full!
        # Otherwise preserve queue unless < 30% full!
        b_keep = 0.75 if (want_b and self.ext_vol_at(bids, want_b[0]) == 0) else 0.30
        a_keep = 0.75 if (want_a and self.ext_vol_at(asks, want_a[0]) == 0) else 0.30

        b_ok = (want_b is None and not cur_b) or (
            want_b is not None and len(cur_b) == 1 and abs(cur_b[0].price - want_b[0]) < EPS
            and max(8, int(want_b[1] * b_keep)) <= cur_b[0].volume <= want_b[1] + 25
            and (want_a is None or cur_b[0].price < want_a[0] - EPS)
        )
        a_ok = (want_a is None and not cur_a) or (
            want_a is not None and len(cur_a) == 1 and abs(cur_a[0].price - want_a[0]) < EPS
            and max(8, int(want_a[1] * a_keep)) <= cur_a[0].volume <= want_a[1] + 25
            and (want_b is None or cur_a[0].price > want_b[0] + EPS)
        )
        if b_ok and a_ok:
            return

        cross_conflict = (
            (not b_ok and want_b and any(o.price <= want_b[0] + EPS for o in cur_a)) or
            (not a_ok and want_a and any(o.price >= want_a[0] + EPS for o in cur_b))
        )
        if cross_conflict or (not b_ok and not a_ok and len(mine) >= 2):
            if not self.spend(reserve=0 if cross_conflict else reserve):
                return
            self.ex.delete_orders(inst)
            cur_b, cur_a = [], []
            b_ok = a_ok = False

        # Execute the crossing/more-urgent side FIRST!
        bb = bids[0][0] if bids else 0.0
        ba = asks[0][0] if asks else 1e9
        ask_crosses = want_a is not None and want_a[0] <= bb + EPS
        sides_order = ("ask", "bid") if ask_crosses else ("bid", "ask")

        for side in sides_order:
            if side == "bid" and not b_ok:
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
            elif side == "ask" and not a_ok:
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
        dt = 0.20 if self.last_t is None else max(0.05, min(1.0, now - self.last_t))
        self.last_t = now

        books, bbo_mids, intact_hm, lead_px, micro, st_obi = {}, {}, {}, {}, {}, {}
        for s in self.insts:
            b, a, _ = self.clean_book(s)
            books[s] = (b, a)
            if b and a:
                bb, bv = b[0]
                ba, av = a[0]
                mid = 0.5 * (bb + ba)
                bbo_mids[s] = mid
                self.mid_hist[s].append((now, mid))
                bvc, avc = min(bv, 110), min(av, 110)
                micro[s] = (bb * avc + ba * bvc) / max(1, bvc + avc) - mid

                cut = 800 if s in FOLLOWERS else 2200
                sb = sum(v for _, v in b if v < cut)
                sa = sum(v for _, v in a if v < cut)
                st_obi[s] = (sb - sa) / (sb + sa) if (sb + sa) > 0 else 0.0

                if s in LEADERS:
                    info = strict_leader_hm(b, a)
                    if info is not None:
                        intact_hm[s] = info
                        lead_px[s] = info[0]
                    else:
                        lead_px[s] = mid
                    self.lead_hist[s].append((now, lead_px[s]))

            sv = 0.0
            if hasattr(self.ex, "poll_new_trade_ticks"):
                for tr in self.ex.poll_new_trade_ticks(s):
                    ag = getattr(tr, "aggressor_side", "")
                    if ag == "bid": sv += float(tr.volume)
                    elif ag == "ask": sv -= float(tr.volume)
            self.flow_ema[s] = math.exp(-dt / 1.2) * self.flow_ema.get(s, 0.0) + sv

        positions = self.ex.get_positions()
        plans = []  # (priority_score, inst, bids, asks, want_b, want_a, reserve)

        for s in ("NBIS", "CRWV", "SNDK"):
            lead, beta = self.PAIRS[s]
            if s not in bbo_mids or lead not in lead_px:
                continue
            raw_a = bbo_mids[s] - beta * lead_px[lead]
            n = self.obs.get(s, 0) + 1
            self.obs[s] = n
            if n == 1:
                self.alpha[s] = raw_a
            elif n <= 15:
                self.alpha[s] = ((n - 1) * self.alpha[s] + raw_a) / n
            else:
                r = self.ALPHA_RATE
                self.alpha[s] = (1.0 - r) * self.alpha[s] + r * raw_a

            fv = self.alpha[s] + beta * lead_px[lead] + self.MICRO_WT * micro[s] + 0.025 * st_obi[s]
            fv += max(-0.06, min(0.06, self.FLOW_WT * self.flow_ema[s]))
            if s in ("CRWV", "NBIS"):
                h = self.mid_hist[s]
                m_1s = bbo_mids[s]
                for ts, mp in reversed(h):
                    if now - ts >= 0.8:
                        m_1s = mp
                        break
                fv += self.AR_WT * (bbo_mids[s] - m_1s)

            bids, asks = books[s]
            bb, ba = bids[0][0], asks[0][0]
            pos = int(positions.get(s, 0))
            res = fv - self.SKEW * pos
            bp = floor_tick(res - self.HALF_SP)
            ap = ceil_tick(res + self.HALF_SP)
            if bp >= ap - EPS:
                ap = r1(bp + TICK)

            bv = max(0, POS_LIMIT - pos) if bp <= fv - self.FLIP_EDGE else max(0, -pos)
            av = max(0, POS_LIMIT + pos) if ap >= fv + self.FLIP_EDGE else max(0, pos)
            if bv + av > MAX_OPEN_VOL:
                if pos > 0:
                    av = min(av, MAX_OPEN_VOL); bv = max(0, min(bv, MAX_OPEN_VOL - av))
                elif pos < 0:
                    bv = min(bv, MAX_OPEN_VOL); av = max(0, min(av, MAX_OPEN_VOL - bv))
                else:
                    bv = min(bv, 100); av = min(av, 100)

            wb = (bp, bv) if bv > 0 else None
            wa = (ap, av) if av > 0 else None
            # Priority score: massive priority when crossing BBO or holding exposed quote!
            urg = 100.0 + abs(fv - 0.5 * (bb + ba)) * 100.0
            plans.append((urg, s, bids, asks, wb, wa, 0))

        if self.USE_LEADERS:
            for s in self.LEAD_ACTIVE:
                if s not in intact_hm:
                    continue
                hm, wall_b, wall_a = intact_hm[s]
                bids, asks = books[s]
                bb, ba = bids[0][0], asks[0][0]
                pos = int(positions.get(s, 0))
                fv = hm + 0.22 * micro[s] + 0.02 * st_obi[s]
                res = fv - self.LEAD_SKEW * pos
                bp = min(r1(wall_a - TICK), floor_tick(res - self.LEAD_HALF_SP))
                ap = max(r1(wall_b + TICK), ceil_tick(res + self.LEAD_HALF_SP))
                want_b = want_a = None
                if pos < self.LEAD_CAP and bp > wall_b + EPS and bp > bb + EPS:
                    want_b = (bp, min(self.LEAD_CAP, self.LEAD_CAP - pos))
                if pos > -self.LEAD_CAP and ap < wall_a - EPS and ap < ba - EPS:
                    want_a = (ap, min(self.LEAD_CAP, self.LEAD_CAP + pos))
                if pos > 0:
                    exit_a = max(r1(wall_b + TICK), ceil_tick(res + 0.015))
                    want_a = (exit_a, pos) if want_a is None else (min(want_a[0], exit_a), max(pos, want_a[1]))
                elif pos < 0:
                    exit_b = min(r1(wall_a - TICK), floor_tick(res - 0.015))
                    want_b = (exit_b, -pos) if want_b is None else (max(want_b[0], exit_b), max(-pos, want_b[1]))
                urg = 10.0 + abs(fv - 0.5 * (bb + ba)) * 50.0 + (20.0 if pos != 0 else 0.0)
                plans.append((urg, s, bids, asks, want_b, want_a, 2))

        plans.sort(key=lambda x: -x[0])
        for _, s, bids, asks, wb, wa, resv in plans:
            if self.budget(resv) <= 0:
                continue
            self.place_quotes(s, bids, asks, wb, wa, reserve=resv)

class B1_All8_032(BreakthroughBase):
    HALF_SP = 0.032; LEAD_HALF_SP = 0.042

class B2_NoAMD_030(BreakthroughBase):
    HALF_SP = 0.030; LEAD_HALF_SP = 0.042; LEAD_ACTIVE = ("AVGO", "NVDA", "MU", "VRT")

class B3_UltraTight_022(BreakthroughBase):
    HALF_SP = 0.022; SKEW = 0.00035; FLIP_EDGE = 0.010; LEAD_HALF_SP = 0.042; LEAD_ACTIVE = ("AVGO", "NVDA", "MU", "VRT")

class B4_JoinTouchLeader(BreakthroughBase):
    """Also allow joining thin leader BBO (bv<=30) when inside wall and edge >= 0.06!"""
    HALF_SP = 0.028; SKEW = 0.00040; FLIP_EDGE = 0.012; LEAD_HALF_SP = 0.042; LEAD_ACTIVE = ("AVGO", "NVDA", "MU", "VRT")
