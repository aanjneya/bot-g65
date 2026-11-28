"""Breakthrough P1..P5: Optimal Penny-Jump (`min(raw_bp, bb + TICK)` when non-crossing, full `raw_bp` when crossing)
+ No-Passive-Flip (`max(-pos, clip)`) + Two-Timescale W_OU + Wide-Corridor All-8-Stock Leader Quoting!"""
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


class PennyJumpBase:
    MAX_REQ = 24
    BASE_CLIP = 40
    ENTRY_HSP = 0.040
    EXIT_HSP = 0.038
    SKEW = 0.00050
    ALPHA_RATE = 0.0015
    MOM_WT = {"SNDK": 0.00, "NBIS": 0.28, "CRWV": 0.28}
    MICRO_WT = 0.28
    SOBI_WT = 0.05
    W_OU = {"SNDK": 1.00, "NBIS": 0.85, "CRWV": 0.60}
    PENNY_LEADERS = False

    ACTIVE_LEADERS = ("AVGO", "NVDA", "VRT", "MU", "AMD")
    # (sweep_hsp, maker_hsp, exit_hsp, cap, maker_clip, pure_ioc_entry, cover_ioc_hsp)
    LEAD_CFG = {
        "AVGO": (0.085, 0.115, 0.065, 100, 35, True,  None),
        "NVDA": (0.055, 0.095, 0.025,  85, 30, True,  None),
        "VRT":  (0.042, 0.042, 0.022, 100, 45, False, None),
        "MU":   (0.042, 0.042, 0.022, 100, 45, False, None),
        "AMD":  (0.085, 0.105, 0.030,  50, 30, False, 0.025),
    }

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

    def place_smart(
        self,
        inst: str,
        unskewed_fv: float,
        bids: List[Tuple[float, int]],
        asks: List[Tuple[float, int]],
        want_b: Optional[Tuple[float, int]],
        want_a: Optional[Tuple[float, int]],
        ot_b: str = "limit",
        ot_a: str = "limit",
        reserve: int = 0,
    ):
        mine = self.ex.get_outstanding_orders(inst)
        pos = int(self.ex.get_positions().get(inst, 0))

        # Safety clamp against Optibook price_change_limit on empty/gapped live books
        ref = 0.5 * (bids[0][0] + asks[0][0]) if (bids and asks and asks[0][0] - bids[0][0] <= 3.0) else unskewed_fv
        if ref and ref > 0:
            max_dev = min(3.5, max(1.5, 0.06 * abs(ref)))
            lo, hi = max(TICK, r1(ref - max_dev)), r1(ref + max_dev)
            if want_b is not None:
                want_b = (r1(max(lo, min(hi, want_b[0]))), want_b[1])
            if want_a is not None:
                want_a = (r1(max(lo, min(hi, want_a[0]))), want_a[1])

        if want_b and want_a and want_b[0] >= want_a[0] - EPS:
            if pos >= 0:
                want_b = (r1(want_a[0] - TICK), want_b[1])
            else:
                want_a = (r1(want_b[0] + TICK), want_a[1])

        cur_b = [o for o in mine.values() if o.side == "bid"]
        cur_a = [o for o in mine.values() if o.side == "ask"]

        def is_side_ok(cur_list, want, ot: str, side: str) -> bool:
            if want is None:
                return len(cur_list) == 0
            if ot != "limit" or len(cur_list) != 1:
                return False
            o = cur_list[0]
            if side == "bid" and want_a and o.price >= want_a[0] - EPS:
                return False
            if side == "ask" and want_b and o.price <= want_b[0] + EPS:
                return False
            if abs(o.price - want[0]) < EPS:
                if o.volume > want[1] + 45:
                    return False
                if o.volume < min(14, want[1] // 2) and self.budget(reserve + 6) > 0:
                    lvls = bids if side == "bid" else asks
                    if sum(v for p, v in lvls if abs(p - o.price) < EPS) == 0:
                        return False
                return o.volume >= 1
            if self.budget(reserve + 5) <= 0 and abs(o.price - want[0]) <= TICK + EPS:
                if side == "bid" and o.price <= unskewed_fv - 0.025 and o.volume >= 4:
                    return True
                if side == "ask" and o.price >= unskewed_fv + 0.025 and o.volume >= 4:
                    return True
            return False

        b_ok = is_side_ok(cur_b, want_b, ot_b, "bid")
        a_ok = is_side_ok(cur_a, want_a, ot_a, "ask")
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
                    if v > 0 and self.spend(0 if ot_b == "ioc" else reserve):
                        self.ex.insert_order(inst, price=want_b[0], volume=int(v), side="bid", order_type=ot_b)

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
                    if v > 0 and self.spend(0 if ot_a == "ioc" else reserve):
                        self.ex.insert_order(inst, price=want_a[0], volume=int(v), side="ask", order_type=ot_a)

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

        positions = self.ex.get_positions()
        fol_plan = []

        for s in ("NBIS", "CRWV", "SNDK"):
            lead, beta, lag_s, hl_s = self.SPECS[s]
            if s not in bbo_mids or lead not in lead_px:
                continue
            hist = self.lead_hist[lead]
            col = 2 if hl_s == 0.50 else 3
            lag_ema = lead_px[lead]
            if hist:
                lag_ema = hist[0][col]
                for rec in reversed(hist):
                    if rec[0] <= now - lag_s + 1e-6:
                        lag_ema = rec[col]
                        break

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

            u_ou = (self.alpha[s] + beta * lag_ema) - cur_m
            upcoming = beta * (lead_px[lead] - lag_ema)
            base_fv = cur_m + self.W_OU[s] * u_ou + upcoming

            m_wt = self.MOM_WT[s]
            mom_term = 0.0
            if m_wt != 0.0:
                h = self.mid_hist[s]
                m_1s = cur_m
                for ts, mp in reversed(h):
                    if now - ts >= 0.8:
                        m_1s = mp
                        break
                mom_term = m_wt * (cur_m - m_1s)

            fv = base_fv + mom_term + self.MICRO_WT * micro[s] + self.SOBI_WT * sobi[s]
            bids, asks = books[s]
            bb, ba = bids[0][0], asks[0][0]
            pos = int(positions.get(s, 0))
            res = fv - self.SKEW * pos
            b_hsp = self.ENTRY_HSP if pos >= 0 else self.EXIT_HSP
            a_hsp = self.ENTRY_HSP if pos <= 0 else self.EXIT_HSP
            raw_bp = floor_tick(res - b_hsp)
            raw_ap = ceil_tick(res + a_hsp)

            # OPTIMAL PENNY-JUMP:
            # If raw_bp crosses ba -> sweep up to raw_bp!
            # Otherwise (passive!) -> NEVER overbid more than 1 tick above best_bid (`min(raw_bp, bb + TICK)`)!
            bp = raw_bp if raw_bp >= ba - EPS else min(raw_bp, r1(bb + TICK))
            ap = raw_ap if raw_ap <= bb + EPS else max(raw_ap, r1(ba - TICK))

            if bp >= ap - EPS:
                if pos >= 0:
                    bp = r1(ap - TICK)
                else:
                    ap = r1(bp + TICK)

            cross_b = sum(v for p, v in asks if p <= bp + EPS)
            cross_a = sum(v for p, v in bids if p >= ap - EPS)

            # NO-PASSIVE-FLIP + NO-1-LOT-STARVATION: `max(-pos, BASE_CLIP)` when pos <= 0!
            if cross_b > 0:
                bv = min(POS_LIMIT - pos, cross_b + self.BASE_CLIP)
            elif fv - bp >= 0.025:
                bv = min(POS_LIMIT - pos, max(-pos, self.BASE_CLIP) if pos < 0 else self.BASE_CLIP)
            else:
                bv = max(0, -pos)

            if cross_a > 0:
                av = min(POS_LIMIT + pos, cross_a + self.BASE_CLIP)
            elif ap - fv >= 0.025:
                av = min(POS_LIMIT + pos, max(pos, self.BASE_CLIP) if pos > 0 else self.BASE_CLIP)
            else:
                av = max(0, pos)

            if bv + av > MAX_OPEN_VOL:
                if cross_b > cross_a:
                    bv = min(bv, MAX_OPEN_VOL); av = max(0, min(av, MAX_OPEN_VOL - bv))
                elif cross_a > cross_b:
                    av = min(av, MAX_OPEN_VOL); bv = max(0, min(av, MAX_OPEN_VOL - av))
                elif pos > 0:
                    av = min(av, MAX_OPEN_VOL); bv = max(0, min(bv, MAX_OPEN_VOL - av))
                elif pos < 0:
                    bv = min(bv, MAX_OPEN_VOL); av = max(0, min(av, MAX_OPEN_VOL - bv))
                else:
                    bv = min(bv, 100); av = min(av, 100)

            urg = max(cross_b * max(0.01, fv - ba), cross_a * max(0.01, bb - fv))
            fol_plan.append((urg, s, fv, bids, asks, (bp, bv) if bv > 0 else None, (ap, av) if av > 0 else None))

        fol_plan.sort(key=lambda x: x[0], reverse=True)
        for _, s, fv, bids, asks, wb, wa in fol_plan:
            self.place_smart(s, fv, bids, asks, wb, wa, "limit", "limit", reserve=0)

        # Leaders with optional Penny-Jumped Wide-Corridor Quoting!
        for s in self.ACTIVE_LEADERS:
            if self.budget(reserve=2) <= 0:
                break
            bids, asks = books.get(s, ([], []))
            if not bids or not asks:
                continue
            bb, ba = bids[0][0], asks[0][0]
            pos = int(self.ex.get_positions().get(s, 0))
            shsp, mhsp, xhsp, cap, mclip, pure_ioc, cover_h = self.LEAD_CFG[s]

            if s not in intact_hm:
                if pos == 0:
                    continue
                fv = 0.5 * (bb + ba)
                wb, wa = bb - 1.0, ba + 1.0
            else:
                hm, wb, wa = intact_hm[s]
                fv = hm + 0.22 * micro[s] + 0.03 * sobi[s]

            if cover_h is not None and s in intact_hm:
                if pos < 0 and fv - ba >= cover_h and ba < wa - EPS:
                    v_cov = min(-pos, sum(v for p, v in asks if p <= floor_tick(fv - cover_h) + EPS))
                    if v_cov > 0:
                        self.place_smart(s, fv, bids, asks, (floor_tick(fv - cover_h), v_cov), None, "ioc", "limit", reserve=1)
                        pos = int(self.ex.get_positions().get(s, 0))
                elif pos > 0 and bb - fv >= cover_h and bb > wb + EPS:
                    v_cov = min(pos, sum(v for p, v in bids if p >= ceil_tick(fv + cover_h) - EPS))
                    if v_cov > 0:
                        self.place_smart(s, fv, bids, asks, None, (ceil_tick(fv + cover_h), v_cov), "limit", "ioc", reserve=1)
                        pos = int(self.ex.get_positions().get(s, 0))

            res = fv - (0.0022 if s == "AMD" else 0.0012) * pos
            sb_px = min(r1(wa - TICK), floor_tick(res - shsp))
            sa_px = max(r1(wb + TICK), ceil_tick(res + shsp))
            cross_b = sum(v for p, v in asks if p <= sb_px + EPS and p < wa - EPS) if s in intact_hm else 0
            cross_a = sum(v for p, v in bids if p >= sa_px - EPS and p > wb + EPS) if s in intact_hm else 0

            want_b = want_a = None
            ot_b = ot_a = "limit"

            allow_maker = (not pure_ioc) or self.PENNY_LEADERS
            if cross_b > 0 and pos < cap:
                if pure_ioc and not self.PENNY_LEADERS:
                    want_b = (sb_px, min(cap - pos, cross_b))
                    ot_b = "ioc"
                else:
                    want_b = (sb_px, min(cap - pos, cross_b + mclip))
            elif allow_maker and s in intact_hm and pos < cap:
                raw_mb = min(r1(wa - TICK), floor_tick(res - mhsp))
                mb_px = min(raw_mb, r1(bb + TICK))
                if mb_px > wb + EPS and mb_px > bb + EPS:
                    want_b = (mb_px, min(mclip, cap - pos))

            if cross_a > 0 and pos > -cap:
                if pure_ioc and not self.PENNY_LEADERS:
                    want_a = (sa_px, min(cap + pos, cross_a))
                    ot_a = "ioc"
                else:
                    want_a = (sa_px, min(cap + pos, cross_a + mclip))
            elif allow_maker and s in intact_hm and pos > -cap:
                raw_ma = max(r1(wb + TICK), ceil_tick(res + mhsp))
                ma_px = max(raw_ma, r1(ba - TICK))
                if ma_px < wa - EPS and ma_px < ba - EPS:
                    want_a = (ma_px, min(mclip, cap + pos))

            if pos > 0 and ot_a != "ioc":
                raw_xa = max(r1(wb + TICK), ceil_tick(res + xhsp))
                exit_a = raw_xa if raw_xa <= bb + EPS else max(raw_xa, r1(ba - TICK))
                want_a = (exit_a, pos) if want_a is None else (min(want_a[0], exit_a), max(pos, want_a[1]))
            elif pos < 0 and ot_b != "ioc":
                raw_xb = min(r1(wa - TICK), floor_tick(res - xhsp))
                exit_b = raw_xb if raw_xb >= ba - EPS else min(raw_xb, r1(bb + TICK))
                want_b = (exit_b, -pos) if want_b is None else (max(want_b[0], exit_b), max(-pos, want_b[1]))

            self.place_smart(s, fv, bids, asks, want_b, want_a, ot_b, ot_a, reserve=2)


class P1_PennyJump_NoFlip(PennyJumpBase):
    BASE_CLIP = 40
    PENNY_LEADERS = False


class P2_PennyJump_All8Leaders(PennyJumpBase):
    BASE_CLIP = 40
    PENNY_LEADERS = True


class P3_PennyJump_Clip60(PennyJumpBase):
    BASE_CLIP = 60
    PENNY_LEADERS = True


class P4_PennyJump_Clip80_Tight(PennyJumpBase):
    BASE_CLIP = 80
    ENTRY_HSP = 0.036
    EXIT_HSP = 0.032
    PENNY_LEADERS = True


class P5_PennyJump_Full95(PennyJumpBase):
    BASE_CLIP = 95
    ENTRY_HSP = 0.038
    EXIT_HSP = 0.035
    PENNY_LEADERS = False
