"""Controlled ablations K1..K5 testing the 4 microstructure breakthroughs past +50,000 PnL:
1) Asymmetric Full-200-Lot IOC Sweeps vs Clipped No-Flip Passive Quoting (eliminates 100-190 lot inter-step jump sweeps)
2) Pure-IOC Stale-Student Sniping + Passive-Unwind-Only on High-Vol Leaders (AMD, AVGO, NVDA, MU, VRT)
3) Lag-Matched Delayed-EMA Alpha Calibration + Anti-Jitter Queue-Preserving Hysteresis + 3-Tier Urgency Scheduler
4) Exact 2.40s SNDK Intermediate Wave Trajectory + Public TradeTick Order-Flow Impulse
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


class Break50kBase:
    MAX_REQ = 24
    ENTRY_CLIP = 35            # Max passive new-risk lots per step (reloads next step; caps inter-step jump loss!)
    ALLOW_PASSIVE_FLIP = False # Never bundle +100 exit AND -100 flip into a 200-lot passive order!
    SWEEP_HSP = 0.035          # Minimum edge to sweep opposite touch aggressively up to +-100
    PASSIVE_ENTRY_HSP = 0.040  # Half-spread for passive entry quotes
    PASSIVE_EXIT_HSP = 0.025   # Half-spread for passive unwind quotes
    HYST_BAND = 0.020          # Keep front-of-queue passive quote if within 1 tick + HYST_BAND & non-toxic
    SKEW = 0.0007
    ALPHA_RATE = 0.0015
    USE_LAG_ALPHA = False
    USE_SNDK_WAVE = False
    USE_FLOW = False
    AR_WT = 0.28
    MICRO_WT = 0.25

    PURE_IOC_LEADERS = False
    ACTIVE_LEADERS = ("AVGO", "AMD", "NVDA", "MU", "VRT")
    LEAD_SWEEP_HSP = {"AVGO": 0.060, "AMD": 0.065, "NVDA": 0.055, "MU": 0.042, "VRT": 0.042}
    LEAD_MAKER_HSP = {"AVGO": 0.095, "AMD": 0.105, "NVDA": 0.085, "MU": 0.045, "VRT": 0.045}
    LEAD_CAP = 100

    SPECS = {
        "SNDK": ("NVDA", 0.4245, 2.40, 0.50),
        "NBIS": ("AVGO", 0.4060, 0.40, 1.00),
        "CRWV": ("MU",   1.1150, 0.00, 1.00),
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

    def pull_toxic(self, inst: str, unskewed_fv: float, tol: float = 0.005):
        mine = self.ex.get_outstanding_orders(inst)
        if not mine:
            return
        bad_b = [o for o in mine.values() if o.side == "bid" and o.price >= unskewed_fv - tol]
        bad_a = [o for o in mine.values() if o.side == "ask" and o.price <= unskewed_fv + tol]
        if bad_b and bad_a:
            if self.spend(0):
                self.ex.delete_orders(inst)
        else:
            for o in bad_b + bad_a:
                if self.spend(0):
                    self.ex.delete_order(inst, order_id=o.order_id)

    def fire_ioc_sweep(self, inst: str, side: str, limit_px: float, opp_levels: list, cap: int = POS_LIMIT):
        pos = int(self.ex.get_positions().get(inst, 0))
        if side == "bid":
            avail = sum(v for p, v in opp_levels if p <= limit_px + EPS)
            want = cap - pos
        else:
            avail = sum(v for p, v in opp_levels if p >= limit_px - EPS)
            want = cap + pos
        vol = min(avail, want)
        if vol <= 0:
            return
        mine = self.ex.get_outstanding_orders(inst)
        opp = "ask" if side == "bid" else "bid"
        cross_own = any(
            o.side == opp and ((side == "bid" and o.price <= limit_px + EPS) or (side == "ask" and o.price >= limit_px - EPS))
            for o in mine.values()
        )
        same_open = sum(o.volume for o in mine.values() if o.side == side)
        headroom = (POS_LIMIT - pos - same_open) if side == "bid" else (POS_LIMIT + pos - same_open)
        tot_open = sum(o.volume for o in mine.values())
        if cross_own or ((headroom < vol or MAX_OPEN_VOL - tot_open < vol) and mine):
            if not self.spend(0):
                return
            self.ex.delete_orders(inst)
            same_open = 0
            tot_open = 0
        pos = int(self.ex.get_positions().get(inst, 0))
        headroom = (POS_LIMIT - pos - same_open) if side == "bid" else (POS_LIMIT + pos - same_open)
        v = min(vol, headroom, MAX_OPEN_VOL - tot_open)
        if v > 0 and self.spend(0):
            self.ex.insert_order(inst, price=r1(limit_px), volume=int(v), side=side, order_type="ioc")

    def sync_passive(
        self,
        inst: str,
        unskewed_fv: float,
        want_b: Optional[Tuple[float, int]],
        want_a: Optional[Tuple[float, int]],
        best_bid: Optional[float],
        best_ask: Optional[float],
        reserve: int = 1,
    ):
        # Strictly passive: clamp inside opposite touch so passive orders never accidentally cross
        if want_b and best_ask is not None and want_b[0] >= best_ask - EPS:
            p = r1(best_ask - TICK)
            want_b = (p, want_b[1]) if p > 0 else None
        if want_a and best_bid is not None and want_a[0] <= best_bid + EPS:
            p = r1(best_bid + TICK)
            want_a = (p, want_a[1])

        pos = int(self.ex.get_positions().get(inst, 0))
        if want_b and want_a and want_b[0] >= want_a[0] - EPS:
            if pos > 0:
                want_b = (r1(want_a[0] - TICK), want_b[1])
            elif pos < 0:
                want_a = (r1(want_b[0] + TICK), want_a[1])
            else:
                want_a = (r1(want_b[0] + TICK), want_a[1])

        mine = self.ex.get_outstanding_orders(inst)
        cur_b = [o for o in mine.values() if o.side == "bid"]
        cur_a = [o for o in mine.values() if o.side == "ask"]

        def side_ok(cur_list, want, side: str) -> bool:
            if want is None:
                return len(cur_list) == 0
            if len(cur_list) != 1:
                return False
            o = cur_list[0]
            if side == "bid" and want_a and o.price >= want_a[0] - EPS:
                return False
            if side == "ask" and want_b and o.price <= want_b[0] + EPS:
                return False
            # Exact price match -> NEVER cancel even 1 lot (preserve front of queue!)
            if abs(o.price - want[0]) < EPS:
                return 1 <= o.volume <= want[1] + 25
            # Anti-jitter hysteresis: if within 1 tick AND still safely positive-EV, keep queue!
            if self.HYST_BAND > 0 and abs(o.price - want[0]) <= TICK + EPS:
                if side == "bid" and o.price <= unskewed_fv - 0.020 and abs(o.price - (want[0])) <= TICK + EPS:
                    # Only keep if target raw edge moved by less than HYST_BAND across tick boundary
                    if o.volume >= max(4, want[1] // 3) and o.volume <= want[1] + 25:
                        return self.budget(reserve + 4) <= 0  # under heavy request load, keep 1-tick neighbor!
                if side == "ask" and o.price >= unskewed_fv + 0.020:
                    if o.volume >= max(4, want[1] // 3) and o.volume <= want[1] + 25:
                        return self.budget(reserve + 4) <= 0
            return False

        b_ok = side_ok(cur_b, want_b, "bid")
        a_ok = side_ok(cur_a, want_a, "ask")
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
        dt = 0.20 if self.last_t is None else max(0.05, min(1.0, now - self.last_t))
        self.last_t = now

        books, bbo_mids, intact_hm, lead_px, micro = {}, {}, {}, {}, {}
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

            if self.USE_FLOW and hasattr(self.ex, "poll_new_trade_ticks"):
                sv = 0.0
                for tr in self.ex.poll_new_trade_ticks(s):
                    ag = getattr(tr, "aggressor_side", "")
                    if ag == "bid":
                        sv += float(tr.volume)
                    elif ag == "ask":
                        sv -= float(tr.volume)
                self.flow_ema[s] = math.exp(-dt / 1.2) * self.flow_ema.get(s, 0.0) + sv

        # Compute Fair Values for Followers
        fol_fv: Dict[str, float] = {}
        for s in ("NBIS", "CRWV", "SNDK"):
            lead, beta, lag_s, hl_s = self.SPECS[s]
            if s not in bbo_mids or lead not in lead_px:
                continue
            if self.USE_LAG_ALPHA and self.lead_hist[lead]:
                col = 2 if hl_s == 0.50 else 3
                ref_lead = self.lead_hist[lead][0][col]
                for rec in reversed(self.lead_hist[lead]):
                    if rec[0] <= now - lag_s + 1e-6:
                        ref_lead = rec[col]
                        break
            else:
                ref_lead = lead_px[lead]

            raw_a = bbo_mids[s] - beta * ref_lead
            n = self.obs.get(s, 0) + 1
            self.obs[s] = n
            if n == 1:
                self.alpha[s] = raw_a
            elif n <= 20:
                self.alpha[s] = ((n - 1) * self.alpha[s] + raw_a) / n
            else:
                r = self.ALPHA_RATE
                self.alpha[s] = (1.0 - r) * self.alpha[s] + r * raw_a

            base_target = self.alpha[s] + beta * lead_px[lead]
            if self.USE_SNDK_WAVE and s == "SNDK" and len(self.lead_hist[lead]) >= 6:
                hist = self.lead_hist[lead]
                lag_ema = hist[0][2]
                for rec in reversed(hist):
                    if rec[0] <= now - 2.40 + 1e-6:
                        lag_ema = rec[2]
                        break
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
                f_1s = self.alpha[s] + beta * sim_ema
                cur_m = bbo_mids[s]
                if (f_1s - cur_m) * (base_target - cur_m) < -0.0025:
                    base_target = f_1s

            fv = base_target + self.MICRO_WT * micro[s]
            if s in ("CRWV", "NBIS"):
                h = self.mid_hist[s]
                m_1s = bbo_mids[s]
                for ts, mp in reversed(h):
                    if now - ts >= 0.8:
                        m_1s = mp
                        break
                fv += self.AR_WT * (bbo_mids[s] - m_1s)
            if self.USE_FLOW:
                fv += max(-0.045, min(0.045, 0.0004 * self.flow_ema.get(s, 0.0)))
            fol_fv[s] = fv

        # TIER 1 (Highest Priority, reserve=0): Pull toxic quotes + fire FULL-CAPACITY IOC sweeps across ALL 8 STOCKS!
        sweep_jobs = []
        positions = self.ex.get_positions()
        for s, fv in fol_fv.items():
            self.pull_toxic(s, fv, tol=0.008)
            bids, asks = books[s]
            pos = int(positions.get(s, 0))
            res = fv - self.SKEW * pos
            if asks and res - asks[0][0] >= self.SWEEP_HSP and pos < POS_LIMIT:
                lim = floor_tick(res - self.SWEEP_HSP + 0.005)
                if lim >= asks[0][0] - EPS:
                    sweep_jobs.append((res - asks[0][0], s, "bid", lim, asks, POS_LIMIT))
            elif bids and bids[0][0] - res >= self.SWEEP_HSP and pos > -POS_LIMIT:
                lim = ceil_tick(res + self.SWEEP_HSP - 0.005)
                if lim <= bids[0][0] + EPS:
                    sweep_jobs.append((bids[0][0] - res, s, "ask", lim, bids, POS_LIMIT))

        for s in self.ACTIVE_LEADERS:
            if s not in intact_hm:
                continue
            hm, wb, wa = intact_hm[s]
            fv = hm + 0.20 * micro[s]
            self.pull_toxic(s, fv, tol=0.015)
            bids, asks = books[s]
            pos = int(positions.get(s, 0))
            res = fv - 0.0012 * pos
            sh = self.LEAD_SWEEP_HSP[s]
            if asks and res - asks[0][0] >= sh and pos < self.LEAD_CAP:
                lim = min(r1(wa - TICK), floor_tick(res - sh + 0.005))
                if lim >= asks[0][0] - EPS:
                    sweep_jobs.append((res - asks[0][0], s, "bid", lim, asks, self.LEAD_CAP))
            elif bids and bids[0][0] - res >= sh and pos > -self.LEAD_CAP:
                lim = max(r1(wb + TICK), ceil_tick(res + sh - 0.005))
                if lim <= bids[0][0] + EPS:
                    sweep_jobs.append((bids[0][0] - res, s, "ask", lim, bids, self.LEAD_CAP))

        sweep_jobs.sort(key=lambda x: x[0], reverse=True)
        for _, s, side, lim, lvls, cap in sweep_jobs:
            self.fire_ioc_sweep(s, side, lim, lvls, cap=cap)

        # TIER 2 (reserve=1): Clipped, No-Flip Passive Quoting on Followers (re-reading fresh book & pos!)
        for s in ("SNDK", "NBIS", "CRWV"):
            if s not in fol_fv or self.budget(reserve=1) <= 0:
                continue
            b, a, _ = self.clean_book(s)
            bb = b[0][0] if b else None
            ba = a[0][0] if a else None
            pos = int(self.ex.get_positions().get(s, 0))
            fv = fol_fv[s]
            res = fv - self.SKEW * pos

            b_hsp = self.PASSIVE_ENTRY_HSP if pos >= 0 else self.PASSIVE_EXIT_HSP
            a_hsp = self.PASSIVE_ENTRY_HSP if pos <= 0 else self.PASSIVE_EXIT_HSP
            bp = floor_tick(res - b_hsp)
            ap = ceil_tick(res + a_hsp)

            # Clipped entry volume + full unwind volume WITHOUT passive flip!
            if pos > 0:
                bv = min(self.ENTRY_CLIP, POS_LIMIT - pos) if bp <= fv - 0.030 else 0
                av = (pos + self.ENTRY_CLIP) if (self.ALLOW_PASSIVE_FLIP and ap >= fv + 0.035) else pos
            elif pos < 0:
                av = min(self.ENTRY_CLIP, POS_LIMIT + pos) if ap >= fv + 0.030 else 0
                bv = (-pos + self.ENTRY_CLIP) if (self.ALLOW_PASSIVE_FLIP and bp <= fv - 0.035) else -pos
            else:
                bv = self.ENTRY_CLIP if bp <= fv - 0.025 else 0
                av = self.ENTRY_CLIP if ap >= fv + 0.025 else 0

            av = min(av, POS_LIMIT + pos, MAX_OPEN_VOL)
            bv = min(bv, POS_LIMIT - pos, MAX_OPEN_VOL - av)
            self.sync_passive(
                s,
                fv,
                (bp, bv) if bv > 0 else None,
                (ap, av) if av > 0 else None,
                bb,
                ba,
                reserve=1,
            )

        # TIER 3 (reserve=3): Leader Passive Unwinds (and optional clipped low-vol inside-spread entries)
        for s in self.ACTIVE_LEADERS:
            if self.budget(reserve=3) <= 0:
                break
            b, a, _ = self.clean_book(s)
            if not b or not a:
                continue
            bb, ba = b[0][0], a[0][0]
            pos = int(self.ex.get_positions().get(s, 0))
            if s not in intact_hm:
                if pos == 0:
                    continue
                fv = 0.5 * (bb + ba)
                wb, wa = bb - 1.0, ba + 1.0
            else:
                hm, wb, wa = intact_hm[s]
                fv = hm + 0.20 * micro.get(s, 0.0)

            res = fv - 0.0015 * pos
            want_b = want_a = None

            if not self.PURE_IOC_LEADERS or s in ("MU", "VRT"):
                mhsp = self.LEAD_MAKER_HSP[s]
                bp = min(r1(wa - TICK), floor_tick(res - mhsp))
                ap = max(r1(wb + TICK), ceil_tick(res + mhsp))
                clip_l = 30
                if pos < self.LEAD_CAP and bp > wb + EPS and bp > bb + EPS:
                    want_b = (bp, min(clip_l, self.LEAD_CAP - pos))
                if pos > -self.LEAD_CAP and ap < wa - EPS and ap < ba - EPS:
                    want_a = (ap, min(clip_l, self.LEAD_CAP + pos))

            # Always post passive inventory exit near fair value when holding leader inventory!
            if pos > 0:
                exit_a = max(r1(wb + TICK), ceil_tick(res + 0.015))
                want_a = (exit_a, pos) if want_a is None else (min(want_a[0], exit_a), max(pos, want_a[1]))
            elif pos < 0:
                exit_b = min(r1(wa - TICK), floor_tick(res - 0.015))
                want_b = (exit_b, -pos) if want_b is None else (max(want_b[0], exit_b), max(-pos, want_b[1]))

            self.sync_passive(s, fv, want_b, want_a, bb, ba, reserve=3)


class K1_ClipPassive_NoFlip(Break50kBase):
    ENTRY_CLIP = 35
    ALLOW_PASSIVE_FLIP = False
    ACTIVE_LEADERS = ("AVGO", "NVDA", "VRT", "MU")
    PURE_IOC_LEADERS = False


class K2_Clip25_PureIOCLeaders_All8(Break50kBase):
    ENTRY_CLIP = 25
    ALLOW_PASSIVE_FLIP = False
    ACTIVE_LEADERS = ("AVGO", "AMD", "NVDA", "VRT", "MU")
    PURE_IOC_LEADERS = True


class K3_LagMatchedAlpha_Hyst(Break50kBase):
    ENTRY_CLIP = 28
    ALLOW_PASSIVE_FLIP = False
    ACTIVE_LEADERS = ("AVGO", "AMD", "NVDA", "VRT", "MU")
    PURE_IOC_LEADERS = True
    USE_LAG_ALPHA = True
    ALPHA_RATE = 0.0020


class K4_SNDKWave_Flow_All8(Break50kBase):
    ENTRY_CLIP = 28
    ALLOW_PASSIVE_FLIP = False
    ACTIVE_LEADERS = ("AVGO", "AMD", "NVDA", "VRT", "MU")
    PURE_IOC_LEADERS = True
    USE_LAG_ALPHA = True
    USE_SNDK_WAVE = True
    USE_FLOW = True
    ALPHA_RATE = 0.0020


class K5_TightSweep025_Clip30(Break50kBase):
    ENTRY_CLIP = 30
    ALLOW_PASSIVE_FLIP = False
    SWEEP_HSP = 0.025
    PASSIVE_ENTRY_HSP = 0.035
    PASSIVE_EXIT_HSP = 0.020
    ACTIVE_LEADERS = ("AVGO", "AMD", "NVDA", "VRT", "MU")
    PURE_IOC_LEADERS = True
    USE_LAG_ALPHA = True
    USE_SNDK_WAVE = True
    USE_FLOW = True
    ALPHA_RATE = 0.0020
