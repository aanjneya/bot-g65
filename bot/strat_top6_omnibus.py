"""Architecture 6: Centered Bayesian Prior+Kalman Meta-Ensemble & Champion (`ApexOmnibusChampion`).

Production Competition Synthesis (Combining All Validated Orthogonal Pillars):
1) High-SNR Centered Bayesian Slope Posterior (`y0 + alpha_c + beta_t * (x - x0)`):
   Starts at structural `beta_0` (`prior_strength = 20.0`) and updates ONLY on large `>= $0.35` noise-free
   House-Wall moves (`2.6s` lag-matched differences) with exact continuity adjustment on `alpha_c`, so it
   experiences ZERO OU drift while adapting automatically if Sunday 10:00 shifts betas!
2) Convex Dip/Spike `2.40s` `SNDK` Delay-Line Envelope (`min`/`max` asymmetric wave pricing)!
3) Block-Diagonal Pair-Factor GLFT Cross-Hedging (`Q_fac = q_lead + beta_t * q_fol`) + Cubic Tail Skew!
4) Two-Tier Sniper-Reserve Capacity (`PASSIVE_CAP = 84`, full `+-100` on crossing sweeps) + Queue-Preserving
   Touch Hysteresis + `amend_order` downsizing + optional `SESSION_DURATION_SEC` final-60s flattener!
"""
from __future__ import annotations

import math
import os
from collections import deque
from typing import Dict, Optional

from strat_common_exec import (
    EPS,
    MAX_OPEN_VOL,
    POS_LIMIT,
    SmartOptibookEngine,
    TICK,
    ceil_tick,
    detect_follower_walls,
    detect_intact_leader_walls,
    floor_tick,
    r1,
)

LEADERS = ("AMD", "AVGO", "MU", "NVDA", "VRT")
FOLLOWERS = ("CRWV", "NBIS", "SNDK")


class CenteredBayesianSlope:
    """High-SNR house-wall jump-gated centered Bayesian slope estimator."""

    __slots__ = ("x0", "sxx", "sxy")

    def __init__(self, x0: float, b0: float, prior_strength: float = 20.0):
        self.x0 = x0
        self.sxx = prior_strength
        self.sxy = prior_strength * b0

    def update(self, dx: float, dy: float):
        if abs(dx) >= 0.35:
            self.sxx = 0.9998 * self.sxx + dx * dx
            self.sxy = 0.9998 * self.sxy + dx * dy

    @property
    def beta(self) -> float:
        return max(0.10, min(2.50, self.sxy / max(1e-6, self.sxx)))


class ApexOmnibusChampion(SmartOptibookEngine):
    BASE_CLIP = 50
    EDGE_SCALE = 200.0
    PASSIVE_CAP = 84
    ENTRY_HSP = 0.040
    EXIT_HSP = 0.037
    GAMMA_ID = 0.00034
    GAMMA_CUBIC = 0.016
    GAMMA_FAC = 0.00026
    ALPHA_RATE = 0.0015
    W_OU = {"SNDK": 1.00, "NBIS": 0.85, "CRWV": 0.60}
    MOM_WT = {"SNDK": 0.00, "NBIS": 0.28, "CRWV": 0.28}

    SPECS = {
        "SNDK": ("NVDA", 0.4248, 2.40, 0.50),
        "NBIS": ("AVGO", 0.4065, 0.40, 1.00),
        "CRWV": ("MU",   1.1180, 0.00, 1.00),
    }
    LEAD_TO_FOL = {"NVDA": ("SNDK", 0.4248), "AVGO": ("NBIS", 0.4065), "MU": ("CRWV", 1.1180)}

    LEAD_CFG = {
        "AVGO": (0.085, 0.115, 0.065, 100, 36),
        "NVDA": (0.055, 0.095, 0.025,  88, 34),
        "VRT":  (0.042, 0.042, 0.022, 100, 48),
        "MU":   (0.042, 0.042, 0.022, 100, 48),
    }

    def __init__(self, ex, folder=None):
        self._init_core(ex)
        self.slope_est: Dict[str, CenteredBayesianSlope] = {}
        self.alpha_c: Dict[str, float] = {}
        self.obs: Dict[str, int] = {}
        self.last_rls_t: Dict[str, float] = {}
        self.mid_hist: Dict[str, deque] = {s: deque(maxlen=40) for s in self.insts}
        self.fhm_hist: Dict[str, deque] = {s: deque(maxlen=40) for s in FOLLOWERS}
        self.lead_hist: Dict[str, deque] = {s: deque(maxlen=70) for s in LEADERS}
        self.lead_ema: Dict[str, Dict[float, float]] = {s: {} for s in LEADERS}
        self.t_start: Optional[float] = None
        self.session_sec = float(os.environ.get("SESSION_DURATION_SEC", "0") or 0.0)
        self.last_t: Optional[float] = None

    def _lookup_lag(self, lead: str, target_t: float, col: int) -> Optional[float]:
        hist = self.lead_hist[lead]
        if not hist:
            return None
        for rec in reversed(hist):
            if rec[0] <= target_t + 1e-6:
                return rec[col]
        return hist[0][col]

    @staticmethod
    def _lookup_series(hist: deque, target_t: float) -> Optional[float]:
        if not hist:
            return None
        for ts, mp in reversed(hist):
            if ts <= target_t + 1e-6:
                return mp
        return hist[0][1]

    def step(self):
        now = self.now()
        if self.t_start is None:
            self.t_start = now
        dt = 0.20 if self.last_t is None else max(0.05, min(1.0, now - self.last_t))
        self.last_t = now

        cap_scale = 1.0
        endgame_skew_mult = 1.0
        if self.session_sec > 0:
            rem = self.session_sec - (now - self.t_start)
            if rem <= 90.0:
                cap_scale = max(0.0, (rem - 15.0) / 75.0)
                endgame_skew_mult = 3.0

        books, bbo_mids, intact_hm, lead_px, micro, sobi = {}, {}, {}, {}, {}, {}
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
            else:
                finfo = detect_follower_walls(b, a)
                self.fhm_hist[s].append((now, finfo[0] if finfo is not None else mid))

        positions = self.ex.get_positions()
        eff_pos_lim = max(0, int(round(POS_LIMIT * cap_scale)))
        eff_pass_cap = max(0, int(round(self.PASSIVE_CAP * cap_scale)))
        fol_plan = []

        for s in ("NBIS", "CRWV", "SNDK"):
            lead, b0, lag_s, hl_s = self.SPECS[s]
            if s not in bbo_mids or lead not in lead_px or not self.lead_hist[lead]:
                continue
            col = 2 if hl_s == 0.50 else 3
            cur_m = bbo_mids[s]
            lag_ema = self._lookup_lag(lead, now - lag_s, col) or lead_px[lead]

            if s not in self.slope_est:
                self.slope_est[s] = CenteredBayesianSlope(lag_ema, b0, prior_strength=20.0)
            est = self.slope_est[s]

            if (now - self.last_rls_t.get(s, -9.0)) >= 0.8 and len(self.fhm_hist[s]) >= 16:
                self.last_rls_t[s] = now
                cur_fhm = self.fhm_hist[s][-1][1]
                old_fhm = self._lookup_series(self.fhm_hist[s], now - 2.60)
                x_old = self._lookup_lag(lead, now - lag_s - 2.60, col)
                if old_fhm is not None and x_old is not None:
                    b_prev = est.beta
                    est.update(lag_ema - x_old, cur_fhm - old_fhm)
                    b_new = est.beta
                    if s in self.alpha_c and abs(b_new - b_prev) > 1e-9:
                        self.alpha_c[s] -= (b_new - b_prev) * (lag_ema - est.x0)

            beta = est.beta
            raw_ac = cur_m - beta * (lag_ema - est.x0)
            n = self.obs.get(s, 0) + 1
            self.obs[s] = n
            if n == 1:
                self.alpha_c[s] = raw_ac
            elif n <= 20:
                self.alpha_c[s] = ((n - 1) * self.alpha_c[s] + raw_ac) / n
            else:
                r = self.ALPHA_RATE
                self.alpha_c[s] = (1.0 - r) * self.alpha_c[s] + r * raw_ac

            u_ou = (self.alpha_c[s] + beta * (lag_ema - est.x0)) - cur_m
            f_term = cur_m + self.W_OU[s] * u_ou + beta * (lead_px[lead] - lag_ema)

            mom_term = 0.0
            if self.MOM_WT[s] != 0.0:
                m_1s = self._lookup_series(self.mid_hist[s], now - 0.8) or cur_m
                mom_term = self.MOM_WT[s] * (cur_m - m_1s)
            adj = mom_term + 0.28 * micro[s] + 0.05 * sobi[s]

            fv_b = fv_a = f_term + adj
            if s == "SNDK" and len(self.lead_hist[lead]) >= 10:
                a_step = 1.0 - math.exp(-0.25 * math.log(2.0) / 0.50)
                sim_e = lag_ema
                for tau in (0.25, 0.50, 0.75, 1.00):
                    lv = self._lookup_lag(lead, now - 2.40 + tau, 1) or lead_px[lead]
                    sim_e = (1.0 - a_step) * sim_e + a_step * lv
                f_1s = cur_m + self.W_OU[s] * u_ou + beta * (sim_e - lag_ema) + adj
                fv_b = min(fv_b, 0.5 * fv_b + 0.5 * f_1s)
                fv_a = max(fv_a, 0.5 * fv_a + 0.5 * f_1s)
            fv = 0.5 * (fv_b + fv_a)

            bids, asks = books[s]
            bb, ba = bids[0][0], asks[0][0]
            pos = int(positions.get(s, 0))
            q_lead = int(positions.get(lead, 0))
            q_fac = q_lead + beta * pos
            u = pos / 100.0
            skew = (self.GAMMA_ID * pos + self.GAMMA_CUBIC * (u * u * u) + self.GAMMA_FAC * beta * q_fac) * endgame_skew_mult

            b_hsp = self.ENTRY_HSP if pos >= 0 else self.EXIT_HSP
            a_hsp = self.ENTRY_HSP if pos <= 0 else self.EXIT_HSP
            raw_bp = floor_tick((fv_b - skew) - b_hsp)
            raw_ap = ceil_tick((fv_a - skew) + a_hsp)
            bp = raw_bp if raw_bp >= ba - EPS else min(raw_bp, r1(bb + TICK))
            ap = raw_ap if raw_ap <= bb + EPS else max(raw_ap, r1(ba - TICK))
            if bp >= ap - EPS:
                if pos >= 0:
                    bp = r1(ap - TICK)
                else:
                    ap = r1(bp + TICK)

            cross_b = sum(v for p, v in asks if p <= bp + EPS)
            cross_a = sum(v for p, v in bids if p >= ap - EPS)
            clip_b = min(75, int(self.BASE_CLIP + self.EDGE_SCALE * max(0.0, (fv_b - bp) - 0.05)))
            clip_a = min(75, int(self.BASE_CLIP + self.EDGE_SCALE * max(0.0, (ap - fv_a) - 0.05)))

            cap_b = eff_pos_lim if (cross_b > 0 or fv_b - bp >= 0.14) else eff_pass_cap
            cap_a = eff_pos_lim if (cross_a > 0 or ap - fv_a >= 0.14) else eff_pass_cap

            if cross_b > 0 and pos < eff_pos_lim:
                bv = min(eff_pos_lim - pos, cross_b + max(0, min(clip_b, cap_b - pos - cross_b)))
                if pos < 0:
                    bv = max(bv, min(eff_pos_lim - pos, -pos))
            elif fv_b - bp >= 0.025 and pos < eff_pos_lim:
                add_b = max(0, cap_b - max(0, pos))
                bv = min(eff_pos_lim - pos, max(-pos, min(clip_b, add_b)) if pos < 0 else min(clip_b, add_b))
            else:
                bv = max(0, -pos)

            if cross_a > 0 and pos > -eff_pos_lim:
                av = min(eff_pos_lim + pos, cross_a + max(0, min(clip_a, cap_a + pos - cross_a)))
                if pos > 0:
                    av = max(av, min(eff_pos_lim + pos, pos))
            elif ap - fv_a >= 0.025 and pos > -eff_pos_lim:
                add_a = max(0, cap_a - max(0, -pos))
                av = min(eff_pos_lim + pos, max(pos, min(clip_a, add_a)) if pos > 0 else min(clip_a, add_a))
            else:
                av = max(0, pos)

            if bv + av > MAX_OPEN_VOL:
                if cross_b > cross_a or pos < 0:
                    bv = min(bv, MAX_OPEN_VOL); av = max(0, min(av, MAX_OPEN_VOL - bv))
                elif cross_a > cross_b or pos > 0:
                    av = min(av, MAX_OPEN_VOL); bv = max(0, min(bv, MAX_OPEN_VOL - av))
                else:
                    bv = min(bv, 100); av = min(av, 100)

            urg = max(cross_b * max(0.01, fv_b - ba), cross_a * max(0.01, bb - fv_a))
            fol_plan.append((urg, s, fv, bids, asks, (bp, bv) if bv > 0 else None, (ap, av) if av > 0 else None))

        fol_plan.sort(key=lambda x: x[0], reverse=True)
        for _, s, fv, bids, asks, wb, wa in fol_plan:
            self.execute_quote_pair(s, fv, bids, asks, wb, wa, "limit", "limit", reserve=0, keep_touch_hyst=True)

        positions = self.ex.get_positions()
        for s in ("AVGO", "NVDA", "VRT", "MU"):
            if self.budget(reserve=2) <= 0:
                break
            bids, asks = books.get(s, ([], []))
            if not bids or not asks:
                continue
            bb, ba = bids[0][0], asks[0][0]
            pos = int(positions.get(s, 0))
            shsp, mhsp, xhsp, raw_cap, mclip = self.LEAD_CFG[s]
            cap = int(round(raw_cap * cap_scale))

            if s not in intact_hm:
                if pos == 0:
                    continue
                fv = 0.5 * (bb + ba)
                wb, wa = bb - 1.0, ba + 1.0
            else:
                hm, wb, wa = intact_hm[s]
                fv = hm + 0.22 * micro[s] + 0.03 * sobi[s]

            fac_adj = 0.0
            if s in self.LEAD_TO_FOL:
                fol, b0 = self.LEAD_TO_FOL[s]
                b_live = self.slope_est[fol].beta if fol in self.slope_est else b0
                q_fac = pos + b_live * int(positions.get(fol, 0))
                fac_adj = max(-0.022, min(0.022, self.GAMMA_FAC * q_fac))

            res = fv - (0.0011 * pos + fac_adj) * endgame_skew_mult
            sb_px = min(r1(wa - TICK), floor_tick(res - shsp))
            sa_px = max(r1(wb + TICK), ceil_tick(res + shsp))
            cross_b = sum(v for p, v in asks if p <= sb_px + EPS and p < wa - EPS) if s in intact_hm else 0
            cross_a = sum(v for p, v in bids if p >= sa_px - EPS and p > wb + EPS) if s in intact_hm else 0

            want_b = want_a = None
            if cross_b > 0 and pos < cap:
                want_b = (sb_px, min(cap - pos, cross_b + mclip))
            elif s in intact_hm and pos < cap:
                mb_px = min(r1(wa - TICK), floor_tick(res - mhsp), r1(bb + TICK))
                if mb_px > wb + EPS and mb_px > bb + EPS:
                    want_b = (mb_px, min(mclip, cap - pos))

            if cross_a > 0 and pos > -cap:
                want_a = (sa_px, min(cap + pos, cross_a + mclip))
            elif s in intact_hm and pos > -cap:
                ma_px = max(r1(wb + TICK), ceil_tick(res + mhsp), r1(ba - TICK))
                if ma_px < wa - EPS and ma_px < ba - EPS:
                    want_a = (ma_px, min(mclip, cap + pos))

            if pos > 0:
                raw_xa = max(r1(wb + TICK), ceil_tick(res + xhsp))
                exit_a = raw_xa if raw_xa <= bb + EPS else max(raw_xa, r1(ba - TICK))
                want_a = (exit_a, pos) if want_a is None else (min(want_a[0], exit_a), max(pos, want_a[1]))
            elif pos < 0:
                raw_xb = min(r1(wa - TICK), floor_tick(res - xhsp))
                exit_b = raw_xb if raw_xb >= ba - EPS else min(raw_xb, r1(bb + TICK))
                want_b = (exit_b, -pos) if want_b is None else (max(want_b[0], exit_b), max(-pos, want_b[1]))

            self.execute_quote_pair(s, fv, bids, asks, want_b, want_a, "limit", "limit", reserve=2)
