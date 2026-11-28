"""Ablations M1..M5 testing the OOS regression & markout breakthroughs to push past +50,000 PnL:
- Unified 1-request crossing-limit sizing `avail_cross + ENTRY_CLIP` (1 req instead of 3, zero passive flip)
- Fixing the conditional sign of 0.8s follower BBO change (`-0.14` fade instead of `+0.28` double-counting!)
- Adding `StudentOBI` (`+0.09` across non-house levels, positive in all 9 OOS regressions)
- Separating `upcoming = beta * (H_lead - LagEMA_lead)` (weight 1.0) from slow `u_OU` (`w_ou`)
- Wide-margin leader sniping + positive-EV exit half-spread (`0.065` on high-vol leaders, `0.042` on followers)
"""
from __future__ import annotations

import math
import time
from collections import deque
from typing import Dict, List, Optional, Tuple

TICK = 0.1
EPS = 1e-9
POS_LIMIT = 100
MAX_OPEN_VOL = 200
LEADERS = ("AMD", "AVGO", "MU", "NVDA", "VRT")
FOLLOWERS = ("CRWV", "NBIS", "SNDK")


def r1(x: float) -> float:
    return round(x + 0.0, 1)


def floor_tick(x: float) -> float:
    return round(math.floor(x / TICK + 1e-9) * TICK, 1)


def ceil_tick(x: float) -> float:
    return round(math.ceil(x / TICK - 1e-9) * TICK, 1)


def strict_leader_hm(bids: List[Tuple[float, int]], asks: List[Tuple[float, int]]) -> Optional[Tuple[float, float, float]]:
    wb = next((p for p, v in bids if v >= 2400), None)
    wa = next((p for p, v in asks if v >= 2400), None)
    if wb is not None and wa is not None and wa > wb + 0.15:
        return 0.5 * (wb + wa), wb, wa
    wb2 = next((p for p, v in bids if v >= 1700), None)
    wa2 = next((p for p, v in asks if v >= 1700), None)
    if wb2 is not None and wa2 is not None and wa2 > wb2 + 0.35:
        return 0.5 * (wb2 + wa2), r1(wb2 + 0.1), r1(wa2 - 0.1)
    return None


def student_obi(inst: str, bids: List[Tuple[float, int]], asks: List[Tuple[float, int]]) -> float:
    cut = 800 if inst in FOLLOWERS else 2200
    sb = sum(v for _, v in bids if v < cut)
    sa = sum(v for _, v in asks if v < cut)
    return (sb - sa) / (sb + sa) if (sb + sa) > 0 else 0.0


class Push55kBase:
    MAX_REQ = 24
    ENTRY_CLIP = 34
    ENTRY_HSP = 0.042
    EXIT_HSP = 0.040
    SKEW = 0.00045
    ALPHA_RATE = 0.0015
    MOM_WT = 0.28              # M1 tests +0.28 vs M2..M5 testing -0.14!
    MICRO_WT = 0.28
    SOBI_WT = 0.00
    FLOW_WT = 0.00
    USE_LAG_ALPHA = False
    USE_SNDK_WAVE = False
    W_OU = {"SNDK": 1.00, "NBIS": 1.00, "CRWV": 1.00}

    ACTIVE_LEADERS = ("AVGO", "NVDA", "VRT", "MU")
    PURE_IOC_HIGHVOL = True
    LEAD_ENTRY_HSP = {"AVGO": 0.085, "AMD": 0.090, "NVDA": 0.075, "MU": 0.042, "VRT": 0.042}
    LEAD_EXIT_HSP  = {"AVGO": 0.065, "AMD": 0.070, "NVDA": 0.055, "MU": 0.025, "VRT": 0.025}
    LEAD_CAP = 100

    SPECS = {
        "SNDK": ("NVDA", 0.4248, 2.40, 0.50),
        "NBIS": ("AVGO", 0.4065, 0.40, 1.00),
        "CRWV": ("MU",   1.1180, 0.00, 1.00),
    }

    def __init__(self, ex, folder=None):
        self.ex = ex
        self.insts = list(getattr(ex, "instruments", [])) or sorted(ex.get_tradable_instruments().keys())
        self.req_ts: deque = deque()
        self.alpha: Dict[str, float] = {}
        self.obs: Dict[str, int] = {}
        self.mid_hist: Dict[str, deque] = {s: deque(maxlen=35) for s in self.insts}
        self.lead_hist: Dict[str, deque] = {s: deque(maxlen=65) for s in LEADERS}
        self.lead_ema: Dict[str, Dict[float, float]] = {s: {} for s in LEADERS}
        self.flow_ema: Dict[str, float] = {s: 0.0 for s in self.insts}
        self.last_t: Optional[float] = None

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

    def place_clipped_unified(
        self,
        inst: str,
        unskewed_fv: float,
        want_b: Optional[Tuple[float, int]],
        want_a: Optional[Tuple[float, int]],
        order_type_b: str = "limit",
        order_type_a: str = "limit",
        reserve: int = 0,
    ):
        """Unified 1-request execution:
        - Crossing orders sweep up to +-100 AND leave at most ENTRY_CLIP resting at queue=0 (or 0 if order_type='ioc')!
        - Never cancels a partially filled order at the right price (MIN_KEEP_LOTS=1)!
        - Zero self-trades guaranteed!"""
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
            want_b is not None and order_type_b == "limit"
            and len(cur_b) == 1 and abs(cur_b[0].price - want_b[0]) < EPS
            and 1 <= cur_b[0].volume <= want_b[1] + 35
            and (want_a is None or cur_b[0].price < want_a[0] - EPS)
        )
        a_ok = (want_a is None and not cur_a) or (
            want_a is not None and order_type_a == "limit"
            and len(cur_a) == 1 and abs(cur_a[0].price - want_a[0]) < EPS
            and 1 <= cur_a[0].volume <= want_a[1] + 35
            and (want_b is None or cur_a[0].price > want_b[0] + EPS)
        )
        if b_ok and a_ok:
            return

        # Under heavy request load, keep 1-tick neighbor passive order if still safely positive-EV!
        if not b_ok and want_b and order_type_b == "limit" and len(cur_b) == 1 and self.budget(reserve + 5) <= 0:
            if abs(cur_b[0].price - want_b[0]) <= TICK + EPS and cur_b[0].price <= unskewed_fv - 0.025:
                if want_a is None or cur_b[0].price < want_a[0] - EPS:
                    b_ok = True
        if not a_ok and want_a and order_type_a == "limit" and len(cur_a) == 1 and self.budget(reserve + 5) <= 0:
            if abs(cur_a[0].price - want_a[0]) <= TICK + EPS and cur_a[0].price >= unskewed_fv + 0.025:
                if want_b is None or cur_a[0].price > want_b[0] + EPS:
                    a_ok = True
        if b_ok and a_ok:
            return

        cross_conflict = (
            (not b_ok and want_b and any(o.price <= want_b[0] + EPS for o in cur_a)) or
            (not a_ok and want_a and any(o.price >= want_a[0] - EPS for o in cur_b))
        )
        if cross_conflict or (not b_ok and not a_ok and len(mine) >= 2):
            if not self.spend(0 if cross_conflict else reserve):
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
                    if v > 0 and self.spend(0 if order_type_b == "ioc" else reserve):
                        self.ex.insert_order(inst, price=want_b[0], volume=int(v), side="bid", order_type=order_type_b)

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
                    if v > 0 and self.spend(0 if order_type_a == "ioc" else reserve):
                        self.ex.insert_order(inst, price=want_a[0], volume=int(v), side="ask", order_type=order_type_a)

    def step(self):
        now = self.now()
        dt = 0.20 if self.last_t is None else max(0.05, min(1.0, now - self.last_t))
        self.last_t = now

        books, bbo_mids, intact_hm, lead_px, micro, sobi = {}, {}, {}, {}, {}, {}
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
                sobi[s] = student_obi(s, b, a)
                if s in LEADERS:
                    info = strict_leader_hm(b, a)
                    if info is not None:
                        intact_hm[s] = info
                        lead_px[s] = info[0]
                    else:
                        lead_px[s] = mid
                    hm = lead_px[s]
                    for hl in (0.50, 1.00):
                        al = 1.0 - math.exp(-dt * math.log(2.0) / hl)
                        self.lead_ema[s][hl] = (1.0 - al) * self.lead_ema[s].get(hl, hm) + al * hm
                    self.lead_hist[s].append((now, hm, self.lead_ema[s][0.50], self.lead_ema[s][1.00]))

            if self.FLOW_WT != 0.0 and hasattr(self.ex, "poll_new_trade_ticks"):
                sv = 0.0
                for tr in self.ex.poll_new_trade_ticks(s):
                    ag = getattr(tr, "aggressor_side", "")
                    if ag == "bid":
                        sv += float(tr.volume)
                    elif ag == "ask":
                        sv -= float(tr.volume)
                self.flow_ema[s] = math.exp(-dt / 1.2) * self.flow_ema.get(s, 0.0) + sv

        positions = self.ex.get_positions()

        # Sort followers dynamically by crossing edge so jumps NEVER miss the rate budget!
        fol_plan = []
        for s in ("NBIS", "CRWV", "SNDK"):
            lead, beta, lag_s, hl_s = self.SPECS[s]
            if s not in bbo_mids or lead not in lead_px:
                continue
            hist = self.lead_hist[lead]
            if self.USE_LAG_ALPHA and hist:
                col = 2 if hl_s == 0.50 else 3
                lag_ema = hist[0][col]
                for rec in reversed(hist):
                    if rec[0] <= now - lag_s + 1e-6:
                        lag_ema = rec[col]
                        break
            else:
                lag_ema = lead_px[lead]

            cur_m = bbo_mids[s]
            raw_a = cur_m - beta * lag_ema
            n = self.obs.get(s, 0) + 1
            self.obs[s] = n
            if n == 1:
                self.alpha[s] = raw_a
            elif n <= 20:
                self.alpha[s] = ((n - 1) * self.alpha[s] + raw_a) / n
            else:
                r = self.ALPHA_RATE
                self.alpha[s] = (1.0 - r) * self.alpha[s] + r * raw_a

            # Two-component decomposition: slow OU equilibrium gap + 100% fast upcoming leader jump!
            u_ou = (self.alpha[s] + beta * lag_ema) - cur_m
            upcoming = beta * (lead_px[lead] - lag_ema)
            w_ou = self.W_OU[s]
            base_fv = cur_m + w_ou * u_ou + upcoming

            if self.USE_SNDK_WAVE and s == "SNDK" and len(hist) >= 6:
                sim_ema = lag_ema
                a_step = 1.0 - math.exp(-0.50 * math.log(2.0) / 0.50)
                for tau in (0.50, 1.00):
                    src_t = now - 2.40 + tau
                    lv = lead_px[lead]
                    for rec in reversed(hist):
                        if rec[0] <= src_t + 1e-6:
                            lv = rec[1]
                            break
                    sim_ema = (1.0 - a_step) * sim_ema + a_step * lv
                f_1s = cur_m + w_ou * u_ou + beta * (sim_ema - lag_ema)
                if (f_1s - cur_m) * (base_fv - cur_m) < -0.0025:
                    base_fv = f_1s

            h = self.mid_hist[s]
            m_1s = cur_m
            for ts, mp in reversed(h):
                if now - ts >= 0.8:
                    m_1s = mp
                    break
            mom_term = self.MOM_WT * (cur_m - m_1s)
            fv = base_fv + mom_term + self.MICRO_WT * micro[s] + self.SOBI_WT * sobi[s]
            if self.FLOW_WT != 0.0:
                fv += max(-0.04, min(0.04, self.FLOW_WT * self.flow_ema.get(s, 0.0)))

            bids, asks = books[s]
            pos = int(positions.get(s, 0))
            res = fv - self.SKEW * pos
            b_hsp = self.ENTRY_HSP if pos >= 0 else self.EXIT_HSP
            a_hsp = self.ENTRY_HSP if pos <= 0 else self.EXIT_HSP
            bp = floor_tick(res - b_hsp)
            ap = ceil_tick(res + a_hsp)
            if bp >= ap - EPS:
                if pos >= 0:
                    bp = r1(ap - TICK)
                else:
                    ap = r1(bp + TICK)

            # Calculate crossing volume vs clipped passive volume!
            cross_b = sum(v for p, v in asks if p <= bp + EPS)
            cross_a = sum(v for p, v in bids if p >= ap - EPS)

            if cross_b > 0:
                bv = min(POS_LIMIT - pos, cross_b + self.ENTRY_CLIP)
            elif pos < 0:
                bv = -pos  # pure unwind to 0 (no passive flip!)
            else:
                bv = min(self.ENTRY_CLIP, POS_LIMIT - pos) if bp <= fv - 0.025 else 0

            if cross_a > 0:
                av = min(POS_LIMIT + pos, cross_a + self.ENTRY_CLIP)
            elif pos > 0:
                av = pos   # pure unwind to 0 (no passive flip!)
            else:
                av = min(self.ENTRY_CLIP, POS_LIMIT + pos) if ap >= fv + 0.025 else 0

            if bv + av > MAX_OPEN_VOL:
                if cross_b > cross_a:
                    bv = min(bv, MAX_OPEN_VOL); av = max(0, min(av, MAX_OPEN_VOL - bv))
                elif cross_a > cross_b:
                    av = min(av, MAX_OPEN_VOL); bv = max(0, min(bv, MAX_OPEN_VOL - av))
                elif pos > 0:
                    av = min(av, MAX_OPEN_VOL); bv = max(0, min(bv, MAX_OPEN_VOL - av))
                else:
                    bv = min(bv, MAX_OPEN_VOL); av = max(0, min(av, MAX_OPEN_VOL - bv))

            urg = max(cross_b * max(0.01, fv - asks[0][0]), cross_a * max(0.01, bids[0][0] - fv))
            fol_plan.append((urg, s, fv, (bp, bv) if bv > 0 else None, (ap, av) if av > 0 else None))

        fol_plan.sort(key=lambda x: x[0], reverse=True)
        for _, s, fv, wb, wa in fol_plan:
            self.place_clipped_unified(s, fv, wb, wa, "limit", "limit", reserve=0)

        # Leaders: Wide-margin student arb + positive-EV unwinds!
        for s in self.ACTIVE_LEADERS:
            if self.budget(reserve=2) <= 0:
                break
            bids, asks = books.get(s, ([], []))
            if not bids or not asks:
                continue
            bb, ba = bids[0][0], asks[0][0]
            pos = int(self.ex.get_positions().get(s, 0))
            if s not in intact_hm:
                if pos == 0:
                    continue
                fv = 0.5 * (bb + ba)
                wb, wa = bb - 1.0, ba + 1.0
            else:
                hm, wb, wa = intact_hm[s]
                fv = hm + 0.22 * micro[s] + 0.04 * sobi[s]

            res = fv - 0.0012 * pos
            ehsp = self.LEAD_ENTRY_HSP[s]
            xhsp = self.LEAD_EXIT_HSP[s]
            bp = min(r1(wa - TICK), floor_tick(res - ehsp))
            ap = max(r1(wb + TICK), ceil_tick(res + ehsp))

            cross_b = sum(v for p, v in asks if p <= bp + EPS and p < wa - EPS) if s in intact_hm else 0
            cross_a = sum(v for p, v in bids if p >= ap - EPS and p > wb + EPS) if s in intact_hm else 0

            want_b = want_a = None
            ot_b = ot_a = "limit"
            is_highvol = self.PURE_IOC_HIGHVOL and s in ("AVGO", "AMD", "NVDA")

            if cross_b > 0 and pos < self.LEAD_CAP:
                if is_highvol:
                    want_b = (bp, min(self.LEAD_CAP - pos, cross_b))
                    ot_b = "ioc"
                else:
                    want_b = (bp, min(self.LEAD_CAP - pos, cross_b + 30))
            elif not is_highvol and s in intact_hm and pos < self.LEAD_CAP and bp > wb + EPS and bp > bb + EPS:
                want_b = (bp, min(35, self.LEAD_CAP - pos))

            if cross_a > 0 and pos > -self.LEAD_CAP:
                if is_highvol:
                    want_a = (ap, min(self.LEAD_CAP + pos, cross_a))
                    ot_a = "ioc"
                else:
                    want_a = (ap, min(self.LEAD_CAP + pos, cross_a + 30))
            elif not is_highvol and s in intact_hm and pos > -self.LEAD_CAP and ap < wa - EPS and ap < ba - EPS:
                want_a = (ap, min(35, self.LEAD_CAP + pos))

            if pos > 0 and ot_a != "ioc":
                exit_a = max(r1(wb + TICK), ceil_tick(res + xhsp))
                want_a = (exit_a, pos) if want_a is None else (min(want_a[0], exit_a), max(pos, want_a[1]))
            elif pos < 0 and ot_b != "ioc":
                exit_b = min(r1(wa - TICK), floor_tick(res - xhsp))
                want_b = (exit_b, -pos) if want_b is None else (max(want_b[0], exit_b), max(-pos, want_b[1]))

            self.place_clipped_unified(s, fv, want_b, want_a, ot_b, ot_a, reserve=2)


class M1_UnifiedClip_FixExit(Push55kBase):
    MOM_WT = 0.28
    SOBI_WT = 0.00
    USE_LAG_ALPHA = False


class M2_FlipMomSign_AddOBI(Push55kBase):
    MOM_WT = -0.14
    MICRO_WT = 0.30
    SOBI_WT = 0.09
    USE_LAG_ALPHA = True


class M3_TwoScale_OU_Upcoming(Push55kBase):
    MOM_WT = -0.14
    MICRO_WT = 0.30
    SOBI_WT = 0.09
    USE_LAG_ALPHA = True
    W_OU = {"SNDK": 0.95, "NBIS": 0.65, "CRWV": 0.60}


class M4_TwoScale_Mild_All8(Push55kBase):
    MOM_WT = -0.14
    MICRO_WT = 0.30
    SOBI_WT = 0.09
    USE_LAG_ALPHA = True
    W_OU = {"SNDK": 1.00, "NBIS": 0.80, "CRWV": 0.78}
    ACTIVE_LEADERS = ("AVGO", "AMD", "NVDA", "VRT", "MU")


class M5_TwoScale_SNDKWave_Tight(Push55kBase):
    ENTRY_CLIP = 38
    ENTRY_HSP = 0.038
    EXIT_HSP = 0.036
    MOM_WT = -0.14
    MICRO_WT = 0.30
    SOBI_WT = 0.09
    FLOW_WT = 0.0003
    USE_LAG_ALPHA = True
    USE_SNDK_WAVE = True
    W_OU = {"SNDK": 1.00, "NBIS": 0.82, "CRWV": 0.80}
    ACTIVE_LEADERS = ("AVGO", "AMD", "NVDA", "VRT", "MU")
