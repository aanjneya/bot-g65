"""Architecture 2: Exact Delay-Line Deconvolution & Multi-Wave Trajectory Rider (`DelayDeconvWaveBot`).

Quantitative Paradigm (Digital Signal Processing / State-Space Filter Deconvolution):
- Models Optiver's exact discrete delay-buffer + IIR low-pass filter pipeline driving each follower:
    * `NVDA -> SNDK`: `2.40s` FIFO delay line + `0.50s` IIR EMA (`beta = 0.4248`)
    * `AVGO -> NBIS`: `0.40s` FIFO delay line + `1.00s` IIR EMA (`beta = 0.4065`)
    * `MU   -> CRWV`: `0.00s` FIFO delay line + `1.00s` IIR EMA (`beta = 1.1180`)
- Forward Trajectory Whipsaw Deconvolution on `SNDK`:
    Propagates the already-observed `NVDA` history `[t - 2.4s .. t]` forward through the exact `0.50s` IIR
    filter to detect large (`>= $0.08`) opposing intermediate waves (`t + 1.0s` vs `t + 2.4s`), riding the
    incoming `1.0s` wave first instead of getting run over before the `2.4s` reversal arrives!
- Full competitive crossing execution + Penny-Jump passive placement + `amend_order` queue preservation!
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


class DelayDeconvWaveBot(SmartOptibookEngine):
    BASE_CLIP = 50
    ENTRY_HSP = 0.040
    EXIT_HSP = 0.038
    SKEW = 0.00050
    ALPHA_RATE = 0.0015
    W_OU = {"SNDK": 1.00, "NBIS": 0.85, "CRWV": 0.60}
    MOM_WT = {"SNDK": 0.00, "NBIS": 0.28, "CRWV": 0.28}

    SPECS = {
        "SNDK": ("NVDA", 0.4248, 2.40, 0.50),
        "NBIS": ("AVGO", 0.4065, 0.40, 1.00),
        "CRWV": ("MU",   1.1180, 0.00, 1.00),
    }

    # (sweep_hsp, maker_hsp, exit_hsp, cap, mclip, pure_ioc)
    LEAD_CFG = {
        "AVGO": (0.085, 0.115, 0.065, 100, 36, False),
        "NVDA": (0.055, 0.095, 0.025,  88, 32, False),
        "VRT":  (0.042, 0.042, 0.022, 100, 48, False),
        "MU":   (0.042, 0.042, 0.022, 100, 48, False),
    }

    def __init__(self, ex, folder=None):
        self._init_core(ex)
        self.alpha: Dict[str, float] = {}
        self.obs: Dict[str, int] = {}
        self.mid_hist: Dict[str, deque] = {s: deque(maxlen=400) for s in self.insts}
        self.lead_hist: Dict[str, deque] = {s: deque(maxlen=700) for s in LEADERS}
        self.lead_ema: Dict[str, Dict[float, float]] = {s: {} for s in LEADERS}
        self.last_t: Optional[float] = None

    def _simulate_sndk_1s_ema(self, hist: deque, now: float, lag_ema: float) -> float:
        a_step = 1.0 - math.exp(-0.25 * math.log(2.0) / 0.50)
        sim_e = lag_ema
        for tau in (0.25, 0.50, 0.75, 1.00):
            src_t = (now - 2.40) + tau
            lv = hist[-1][1]
            for rec in reversed(hist):
                if rec[0] <= src_t + 1e-6:
                    lv = rec[1]
                    break
            sim_e = (1.0 - a_step) * sim_e + a_step * lv
        return sim_e

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
                cut = 800 if s in FOLLOWERS else 2200
                sb = sum(v for _, v in b if v < cut)
                sa = sum(v for _, v in a if v < cut)
                sobi[s] = (sb - sa) / (sb + sa) if (sb + sa) > 0 else 0.0

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
            if s not in bbo_mids or lead not in lead_px or not self.lead_hist[lead]:
                continue
            hist = self.lead_hist[lead]
            col = 2 if hl_s == 0.50 else 3
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
            f_term = cur_m + self.W_OU[s] * u_ou + beta * (lead_px[lead] - lag_ema)

            mom_term = 0.0
            if self.MOM_WT[s] != 0.0:
                m_1s = cur_m
                for ts, mp in reversed(self.mid_hist[s]):
                    if now - ts >= 0.8:
                        m_1s = mp
                        break
                mom_term = self.MOM_WT[s] * (cur_m - m_1s)

            adj = mom_term + 0.28 * micro[s] + 0.05 * sobi[s]
            fv_b = fv_a = f_term + adj
            if s == "SNDK" and len(hist) >= 10:
                e_1s = self._simulate_sndk_1s_ema(hist, now, lag_ema)
                f_1s = cur_m + self.W_OU[s] * u_ou + beta * (e_1s - lag_ema) + adj
                # Convex dip/spike envelope: wait for cheaper fill if 1s wave dips; hold higher ask if 1s wave spikes!
                fv_b = min(fv_b, 0.5 * fv_b + 0.5 * f_1s)
                fv_a = max(fv_a, 0.5 * fv_a + 0.5 * f_1s)
            fv = 0.5 * (fv_b + fv_a)

            bids, asks = books[s]
            bb, ba = bids[0][0], asks[0][0]
            pos = int(positions.get(s, 0))
            b_hsp = self.ENTRY_HSP if pos >= 0 else self.EXIT_HSP
            a_hsp = self.ENTRY_HSP if pos <= 0 else self.EXIT_HSP
            raw_bp = floor_tick((fv_b - self.SKEW * pos) - b_hsp)
            raw_ap = ceil_tick((fv_a - self.SKEW * pos) + a_hsp)

            bp = raw_bp if raw_bp >= ba - EPS else min(raw_bp, r1(bb + TICK))
            ap = raw_ap if raw_ap <= bb + EPS else max(raw_ap, r1(ba - TICK))
            if bp >= ap - EPS:
                if pos >= 0:
                    bp = r1(ap - TICK)
                else:
                    ap = r1(bp + TICK)

            cross_b = sum(v for p, v in asks if p <= bp + EPS)
            cross_a = sum(v for p, v in bids if p >= ap - EPS)

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
                fv = hm + 0.22 * micro[s] + 0.03 * sobi[s]

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
