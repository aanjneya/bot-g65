"""Architecture 4: Multi-Level Student-LOB Microstructure, OFI & Trade-Tape Penny-Jumper (`StudentBookMicroPennyBot`).

Quantitative Paradigm (Order-Book Microstructure, Queue-Theory & Live Tape Alpha):
- Strips LivelyLasagnas house walls to extract the pure multi-level Student Limit Order Book,
  fusing FOUR real-time microstructure alpha streams across all 8 stocks:
    1) Distance-Weighted Multi-Level Student OBI (`exp(-dist / 0.18)` across non-house levels)
    2) Volume-Capped Stoikov Microprice (`cap = 110` lots)
    3) 1.5-second BBO Order-Flow Imbalance (`OFI_1.5s`)
    4) Live `poll_new_trade_ticks` Signed Aggressor Tape Impulse (`TradeFlow`)
- Pairs Edge-Proportional Dynamic Clipping (`40..75` lots) with `amend_order` queue preservation!
"""
from __future__ import annotations

import math
from collections import deque
from typing import Dict, List, Optional, Tuple

from strat_common_exec import (
    EPS,
    MAX_OPEN_VOL,
    POS_LIMIT,
    SmartOptibookEngine,
    TICK,
    ceil_tick,
    detect_intact_leader_walls,
    floor_tick,
    r1,
)

LEADERS = ("AMD", "AVGO", "MU", "NVDA", "VRT")
FOLLOWERS = ("CRWV", "NBIS", "SNDK")


def weighted_student_obi(inst: str, bids: List[Tuple[float, int]], asks: List[Tuple[float, int]], mid: float) -> float:
    cut = 800 if inst in FOLLOWERS else 2200
    wb_sum = 0.0
    wa_sum = 0.0
    for p, v in bids:
        if v < cut:
            wb_sum += v * math.exp(-max(0.0, mid - p) / 0.18)
    for p, v in asks:
        if v < cut:
            wa_sum += v * math.exp(-max(0.0, p - mid) / 0.18)
    tot = wb_sum + wa_sum
    return (wb_sum - wa_sum) / tot if tot > 0 else 0.0


class StudentBookMicroPennyBot(SmartOptibookEngine):
    BASE_CLIP = 48
    EDGE_SCALE = 200.0
    ENTRY_HSP = 0.036
    EXIT_HSP = 0.032
    SKEW = 0.00055
    ALPHA_RATE = 0.0015
    W_OU = {"SNDK": 1.00, "NBIS": 0.80, "CRWV": 0.56}

    SPECS = {
        "SNDK": ("NVDA", 0.4248, 2.40, 0.50),
        "NBIS": ("AVGO", 0.4065, 0.40, 1.00),
        "CRWV": ("MU",   1.1180, 0.00, 1.00),
    }

    LEAD_CFG = {
        "AVGO": (0.085, 0.115, 0.065, 100, 36, False),
        "NVDA": (0.055, 0.095, 0.025,  88, 34, False),
        "VRT":  (0.042, 0.042, 0.022, 100, 48, False),
        "MU":   (0.042, 0.042, 0.022, 100, 48, False),
    }

    def __init__(self, ex, folder=None):
        self._init_core(ex)
        self.alpha: Dict[str, float] = {}
        self.obs: Dict[str, int] = {}
        self.prev_bbo: Dict[str, Tuple[float, int, float, int]] = {}
        self.ofi_ema: Dict[str, float] = {s: 0.0 for s in self.insts}
        self.flow_ema: Dict[str, float] = {s: 0.0 for s in self.insts}
        self.mid_hist: Dict[str, deque] = {s: deque(maxlen=350) for s in self.insts}
        self.lead_hist: Dict[str, deque] = {s: deque(maxlen=650) for s in LEADERS}
        self.lead_ema: Dict[str, Dict[float, float]] = {s: {} for s in LEADERS}
        self.last_t: Optional[float] = None

    def step(self):
        now = self.now()
        dt = 0.20 if self.last_t is None else max(0.05, min(1.0, now - self.last_t))
        self.last_t = now

        books, bbo_mids, intact_hm, lead_px, micro, wobi, ofi_sig, tape_sig = {}, {}, {}, {}, {}, {}, {}, {}
        for s in self.insts:
            b, a, _ = self.clean_book(s)
            books[s] = (b, a)
            sv = 0.0
            if hasattr(self.ex, "poll_new_trade_ticks"):
                for tr in self.ex.poll_new_trade_ticks(s):
                    ag = getattr(tr, "aggressor_side", "")
                    if ag == "bid":
                        sv += float(tr.volume)
                    elif ag == "ask":
                        sv -= float(tr.volume)
            self.flow_ema[s] = math.exp(-dt / 1.2) * self.flow_ema.get(s, 0.0) + sv

            if b and a:
                bb, raw_bv = b[0]
                ba, raw_av = a[0]
                bv, av = min(raw_bv, 110), min(raw_av, 110)
                mid = 0.5 * (bb + ba)
                bbo_mids[s] = mid
                self.mid_hist[s].append((now, mid))

                if s in self.prev_bbo:
                    pbb, pbv, pba, pav = self.prev_bbo[s]
                    db = (bv if bb >= pbb else 0) - (pbv if bb <= pbb else 0)
                    da = (av if ba <= pba else 0) - (pav if ba >= pba else 0)
                    self.ofi_ema[s] = 0.84 * self.ofi_ema.get(s, 0.0) + 0.16 * float(db - da)
                self.prev_bbo[s] = (bb, bv, ba, av)

                micro[s] = (bb * av + ba * bv) / max(1, bv + av) - mid
                wobi[s] = weighted_student_obi(s, b, a, mid)
                ofi_sig[s] = max(-0.015, min(0.015, 0.00025 * self.ofi_ema[s]))
                tape_sig[s] = max(-0.015, min(0.015, 0.00012 * self.flow_ema[s]))

                if s in LEADERS:
                    info = detect_intact_leader_walls(b, a)
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
                r = 1.0 - (1.0 - self.ALPHA_RATE) ** (dt / 0.20)
                self.alpha[s] = (1.0 - r) * self.alpha[s] + r * raw_a

            u_ou = (self.alpha[s] + beta * lag_ema) - cur_m
            upcoming = beta * (lead_px[lead] - lag_ema)
            mom_term = 0.0
            if s in ("CRWV", "NBIS"):
                m_1s = cur_m
                for ts, mp in reversed(self.mid_hist[s]):
                    if now - ts >= 0.8:
                        m_1s = mp
                        break
                mom_term = 0.27 * (cur_m - m_1s)

            fv = (
                cur_m
                + self.W_OU[s] * u_ou
                + upcoming
                + mom_term
                + 0.28 * micro[s]
                + 0.055 * wobi[s]
                + ofi_sig[s]
                + tape_sig[s]
            )
            bids, asks = books[s]
            bb, ba = bids[0][0], asks[0][0]
            pos = int(positions.get(s, 0))
            res = fv - self.SKEW * pos
            b_hsp = self.ENTRY_HSP if pos >= 0 else self.EXIT_HSP
            a_hsp = self.ENTRY_HSP if pos <= 0 else self.EXIT_HSP
            raw_bp = floor_tick(res - b_hsp)
            raw_ap = ceil_tick(res + a_hsp)

            bp = raw_bp if raw_bp >= ba - EPS else min(raw_bp, r1(bb + TICK))
            ap = raw_ap if raw_ap <= bb + EPS else max(raw_ap, r1(ba - TICK))
            if bp >= ap - EPS:
                if pos >= 0:
                    bp = r1(ap - TICK)
                else:
                    ap = r1(bp + TICK)

            cross_b = sum(v for p, v in asks if p <= bp + EPS)
            cross_a = sum(v for p, v in bids if p >= ap - EPS)
            clip_b = min(75, int(self.BASE_CLIP + self.EDGE_SCALE * max(0.0, (fv - bp) - 0.05)))
            clip_a = min(75, int(self.BASE_CLIP + self.EDGE_SCALE * max(0.0, (ap - fv) - 0.05)))

            if cross_b > 0:
                bv = min(POS_LIMIT - pos, cross_b + clip_b)
            elif fv - bp >= 0.025:
                bv = min(POS_LIMIT - pos, max(-pos, clip_b) if pos < 0 else clip_b)
            else:
                bv = max(0, -pos)

            if cross_a > 0:
                av = min(POS_LIMIT + pos, cross_a + clip_a)
            elif ap - fv >= 0.025:
                av = min(POS_LIMIT + pos, max(pos, clip_a) if pos > 0 else clip_a)
            else:
                av = max(0, pos)

            if bv + av > MAX_OPEN_VOL:
                if cross_b > cross_a or pos < 0:
                    bv = min(bv, MAX_OPEN_VOL); av = max(0, min(av, MAX_OPEN_VOL - bv))
                elif cross_a > cross_b or pos > 0:
                    av = min(av, MAX_OPEN_VOL); bv = max(0, min(bv, MAX_OPEN_VOL - av))
                else:
                    bv = min(bv, 100); av = min(av, 100)

            urg = max(cross_b * max(0.01, fv - ba), cross_a * max(0.01, bb - fv))
            fol_plan.append((urg, s, fv, bids, asks, (bp, bv) if bv > 0 else None, (ap, av) if av > 0 else None))

        fol_plan.sort(key=lambda x: x[0], reverse=True)
        for _, s, fv, bids, asks, wb, wa in fol_plan:
            self.execute_quote_pair(s, fv, bids, asks, wb, wa, "limit", "limit", reserve=0, keep_touch_hyst=True)

        for s in ("AVGO", "NVDA", "VRT", "MU"):
            if self.budget(reserve=2) <= 0:
                break
            bids, asks = books.get(s, ([], []))
            if not bids or not asks:
                continue
            bb, ba = bids[0][0], asks[0][0]
            pos = int(self.ex.get_positions().get(s, 0))
            shsp, mhsp, xhsp, cap, mclip, pure_ioc = self.LEAD_CFG[s]

            if s not in intact_hm:
                if pos == 0:
                    continue
                fv = 0.5 * (bb + ba)
                wb, wa = bb - 1.0, ba + 1.0
            else:
                hm, wb, wa = intact_hm[s]
                fv = hm + 0.22 * micro[s] + 0.04 * wobi[s] + 0.5 * ofi_sig[s]

            res = fv - (0.0020 if s == "AMD" else 0.0012) * pos
            sb_px = min(r1(wa - TICK), floor_tick(res - shsp))
            sa_px = max(r1(wb + TICK), ceil_tick(res + shsp))
            cross_b = sum(v for p, v in asks if p <= sb_px + EPS and p < wa - EPS) if s in intact_hm else 0
            cross_a = sum(v for p, v in bids if p >= sa_px - EPS and p > wb + EPS) if s in intact_hm else 0

            want_b = want_a = None
            ot_b = ot_a = "limit"
            if cross_b > 0 and pos < cap:
                if pure_ioc:
                    want_b = (sb_px, min(cap - pos, cross_b)); ot_b = "ioc"
                else:
                    want_b = (sb_px, min(cap - pos, cross_b + mclip))
            elif not pure_ioc and s in intact_hm and pos < cap:
                mb_px = min(r1(wa - TICK), floor_tick(res - mhsp), r1(bb + TICK))
                if mb_px > wb + EPS and mb_px > bb + EPS:
                    want_b = (mb_px, min(mclip, cap - pos))

            if cross_a > 0 and pos > -cap:
                if pure_ioc:
                    want_a = (sa_px, min(cap + pos, cross_a)); ot_a = "ioc"
                else:
                    want_a = (sa_px, min(cap + pos, cross_a + mclip))
            elif not pure_ioc and s in intact_hm and pos > -cap:
                ma_px = max(r1(wb + TICK), ceil_tick(res + mhsp), r1(ba - TICK))
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

            self.execute_quote_pair(s, fv, bids, asks, want_b, want_a, ot_b, ot_a, reserve=2)
