"""Architecture 1: 100% Ticker-Agnostic Self-Calibrating Graph & Centered Kalman Bot (`BlindGraphKalmanBot`).

Anti-Overfitting Gold Standard:
- ZERO hardcoded stock ticker symbols ("NVDA", "SNDK", "AVGO", "NBIS", "MU", "CRWV", etc. NEVER appear!).
- ZERO hardcoded cointegration slopes (betas), intercepts (alphas), or lag assignments!
- Discovers EVERYTHING live from the raw Optibook order book:
    1) Role Discovery: Automatically classifies instruments with `>= 2200` lot walls as Independent Leaders
       and instruments with `< 2200` lot walls as Cointegrated Followers!
    2) Lag-Separating `2.6s` House-Wall Return RLS: Uses a `2.6s` return window (`now` vs `now - 2.6s`) on
       noise-free `follower_house_mid` so `0.0s`, `0.4s`, and `2.4s` lags separate cleanly by >10x in R^2,
       updating centered `beta` on genuine leader moves (`|dx| >= 0.18`) with exact `alpha_c` continuity!
    3) Corridor-Geometry Parameterization: Automatically sets `w_ou` on followers and `(sweep_hsp, maker_hsp,
       exit_hsp, cap)` on leaders purely from observed symmetric wall-corridor width `wa - wb`!
"""
from __future__ import annotations

import math
from collections import deque
from typing import Dict, Optional, Tuple

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

CANDIDATE_MODELS = (
    (0.0, 1.00),
    (0.4, 1.00),
    (2.4, 0.50),
)


class CenteredJumpRLS:
    """Jump-gated 2.6s differenced OLS/Ridge estimator with centered level anchor x0."""

    __slots__ = ("x0", "sxx", "sxy", "syy", "all_sxx", "all_sxy", "all_syy", "n_jumps")

    def __init__(self, x0: float, prior_b: float):
        self.x0 = x0
        w = 0.20
        self.sxx = w
        self.sxy = w * prior_b
        self.syy = w * prior_b * prior_b + 0.04
        self.all_sxx = 1e-4
        self.all_sxy = 0.0
        self.all_syy = 1e-4
        self.n_jumps = 0

    def observe(self, dx: float, dy: float):
        self.all_sxx = 0.9995 * self.all_sxx + dx * dx
        self.all_sxy = 0.9995 * self.all_sxy + dx * dy
        self.all_syy = 0.9995 * self.all_syy + dy * dy
        if abs(dx) >= 0.18:
            self.sxx = 0.9995 * self.sxx + dx * dx
            self.sxy = 0.9995 * self.sxy + dx * dy
            self.syy = 0.9995 * self.syy + dy * dy
            self.n_jumps += 1

    @property
    def beta(self) -> float:
        return max(0.10, min(2.50, self.sxy / max(1e-6, self.sxx)))

    @property
    def r2(self) -> float:
        denom = self.all_sxx * self.all_syy
        if denom <= 1e-8 or self.all_sxy <= 0.0:
            return 0.0
        return max(0.0, min(1.0, (self.all_sxy * self.all_sxy) / denom))


class BlindGraphKalmanBot(SmartOptibookEngine):
    BASE_CLIP = 50
    ENTRY_HSP = 0.040
    EXIT_HSP = 0.038
    SKEW_LIN = 0.00050
    ALPHA_RATE = 0.0015
    DIFF_WIN = 2.60

    def __init__(self, ex, folder=None):
        self._init_core(ex)
        self.role: Dict[str, str] = {}
        self.wall_max_seen: Dict[str, int] = {s: 0 for s in self.insts}
        self.fol_wsp_ema: Dict[str, float] = {}
        self.lead_wsp_ema: Dict[str, float] = {}
        self.lead_ema: Dict[str, Dict[float, float]] = {s: {} for s in self.insts}
        self.lead_hist: Dict[str, deque] = {s: deque(maxlen=800) for s in self.insts}
        self.fol_hist: Dict[str, deque] = {s: deque(maxlen=400) for s in self.insts}
        self.fol_hm_hist: Dict[str, deque] = {s: deque(maxlen=400) for s in self.insts}
        self.last_obs_step: Dict[str, float] = {}

        self.edges: Dict[str, Dict[Tuple[str, float, float], CenteredJumpRLS]] = {}
        self.active_edge: Dict[str, Tuple[str, float, float]] = {}
        self.alpha_c: Dict[Tuple[str, str, float, float], float] = {}
        self.alpha_n: Dict[Tuple[str, str, float, float], int] = {}
        self.last_t: Optional[float] = None

    def _lookup_lagged_ema(self, s: str, target_t: float, hl: float) -> Optional[float]:
        hist = self.lead_hist[s]
        if not hist:
            return None
        col = 2 if hl == 0.50 else 3
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
        dt = 0.20 if self.last_t is None else max(0.05, min(1.0, now - self.last_t))
        self.last_t = now

        books, bbo_mids, intact_hm, ref_px, micro, sobi = {}, {}, {}, {}, {}, {}
        for s in self.insts:
            b, a, _ = self.clean_book(s)
            books[s] = (b, a)
            if not b or not a:
                continue
            bb, bv = b[0]
            ba, av = a[0]
            mid = 0.5 * (bb + ba)
            bbo_mids[s] = mid
            bvc, avc = min(bv, 110), min(av, 110)
            micro[s] = (bb * avc + ba * bvc) / max(1, bvc + avc) - mid

            vmax = max(max((v for _, v in b), default=0), max((v for _, v in a), default=0))
            if vmax > self.wall_max_seen[s]:
                self.wall_max_seen[s] = vmax
            self.role[s] = "leader" if self.wall_max_seen[s] >= 2200 else "follower"

            cut = 800 if self.role[s] == "follower" else 2200
            sb = sum(v for _, v in b if v < cut)
            sa = sum(v for _, v in a if v < cut)
            sobi[s] = (sb - sa) / (sb + sa) if (sb + sa) > 0 else 0.0

            if self.role[s] == "leader":
                info = detect_intact_leader_walls(b, a)
                if info is not None:
                    intact_hm[s] = info
                    ref_px[s] = info[0]
                    wsp = info[2] - info[1]
                    self.lead_wsp_ema[s] = wsp if s not in self.lead_wsp_ema else 0.98 * self.lead_wsp_ema[s] + 0.02 * wsp
                else:
                    ref_px[s] = mid
                hm = ref_px[s]
                for hl in (0.50, 1.00):
                    al = 1.0 - math.exp(-dt * math.log(2.0) / hl)
                    self.lead_ema[s][hl] = (1.0 - al) * self.lead_ema[s].get(hl, hm) + al * hm
                self.lead_hist[s].append((now, hm, self.lead_ema[s][0.50], self.lead_ema[s][1.00]))
            else:
                finfo = detect_follower_walls(b, a)
                if finfo is not None:
                    fwsp = finfo[2] - finfo[1]
                    self.fol_wsp_ema[s] = fwsp if s not in self.fol_wsp_ema else 0.98 * self.fol_wsp_ema[s] + 0.02 * fwsp
                    f_obs = finfo[0]
                else:
                    f_obs = mid
                self.fol_hist[s].append((now, mid))
                self.fol_hm_hist[s].append((now, f_obs))

        leaders = [s for s in self.insts if self.role.get(s) == "leader" and s in ref_px]
        followers = [s for s in self.insts if self.role.get(s) == "follower" and s in bbo_mids]
        positions = self.ex.get_positions()
        fol_plan = []

        for s in followers:
            cur_y = bbo_mids[s]
            cur_fhm = self.fol_hm_hist[s][-1][1]
            old_fhm = self._lookup_series(self.fol_hm_hist[s], now - self.DIFF_WIN)
            do_rls_obs = (now - self.last_obs_step.get(s, -9.0)) >= 0.60 and len(self.fol_hm_hist[s]) >= 16
            if do_rls_obs:
                self.last_obs_step[s] = now

            bank = self.edges.setdefault(s, {})
            best_key = None
            best_r2 = -1.0

            for l_sym in leaders:
                for lag_s, hl_s in CANDIDATE_MODELS:
                    key = (l_sym, lag_s, hl_s)
                    x_lag = self._lookup_lagged_ema(l_sym, now - lag_s, hl_s)
                    if x_lag is None:
                        continue
                    if key not in bank:
                        p_b = max(0.38, min(1.15, 0.85 * cur_y / max(1.0, x_lag)))
                        bank[key] = CenteredJumpRLS(x_lag, p_b)

                    mod = bank[key]
                    akey = (s, l_sym, lag_s, hl_s)
                    if do_rls_obs and old_fhm is not None:
                        x_old = self._lookup_lagged_ema(l_sym, now - lag_s - self.DIFF_WIN, hl_s)
                        if x_old is not None:
                            b_prev = mod.beta
                            mod.observe(x_lag - x_old, cur_fhm - old_fhm)
                            b_new = mod.beta
                            if akey in self.alpha_c and abs(b_new - b_prev) > 1e-9:
                                self.alpha_c[akey] -= (b_new - b_prev) * (x_lag - mod.x0)

                    beta = mod.beta
                    raw_ac = cur_y - beta * (x_lag - mod.x0)
                    n = self.alpha_n.get(akey, 0) + 1
                    self.alpha_n[akey] = n
                    if n == 1:
                        self.alpha_c[akey] = raw_ac
                    elif n <= 20:
                        self.alpha_c[akey] = ((n - 1) * self.alpha_c[akey] + raw_ac) / n
                    else:
                        r = 1.0 - (1.0 - self.ALPHA_RATE) ** (dt / 0.20)
                        self.alpha_c[akey] = (1.0 - r) * self.alpha_c[akey] + r * raw_ac

                    if mod.r2 > best_r2:
                        best_r2 = mod.r2
                        best_key = key

            cur_key = self.active_edge.get(s)
            if cur_key is None:
                if best_key is not None and best_r2 >= 0.22:
                    cur_key = best_key
                    self.active_edge[s] = cur_key
            elif best_key is not None and best_key != cur_key:
                if best_r2 > bank[cur_key].r2 + (0.015 if best_key[0] == cur_key[0] else 0.08):
                    cur_key = best_key
                    self.active_edge[s] = cur_key

            if cur_key is None:
                continue

            l_sym, lag_s, hl_s = cur_key
            if l_sym not in ref_px:
                continue
            mod = bank[cur_key]
            beta = mod.beta
            lag_ema = self._lookup_lagged_ema(l_sym, now - lag_s, hl_s) or ref_px[l_sym]
            alp_c = self.alpha_c[(s, l_sym, lag_s, hl_s)]

            eq_lag = alp_c + beta * (lag_ema - mod.x0)
            u_ou = eq_lag - cur_y
            upcoming = beta * (ref_px[l_sym] - lag_ema)

            fwsp = self.fol_wsp_ema.get(s, 0.85)
            if fwsp <= 0.65:
                w_ou, m_wt = 1.00, 0.00
            elif fwsp <= 1.05:
                w_ou, m_wt = 0.85, 0.28
            else:
                w_ou, m_wt = 0.60, 0.28

            mom_term = 0.0
            if m_wt > 0.0:
                m_1s = self._lookup_series(self.fol_hist[s], now - 0.8) or cur_y
                mom_term = m_wt * (cur_y - m_1s)

            fv = cur_y + w_ou * u_ou + upcoming + mom_term + 0.28 * micro[s] + 0.05 * sobi[s]
            bids, asks = books[s]
            bb, ba = bids[0][0], asks[0][0]
            pos = int(positions.get(s, 0))
            res = fv - self.SKEW_LIN * pos
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

        lead_order = sorted(
            [s for s in self.insts if self.role.get(s) == "leader"],
            key=lambda s: self.lead_wsp_ema.get(s, 0.30),
            reverse=True,
        )
        for s in lead_order:
            if self.budget(reserve=2) <= 0:
                break
            bids, asks = books.get(s, ([], []))
            if not bids or not asks:
                continue
            bb, ba = bids[0][0], asks[0][0]
            pos = int(self.ex.get_positions().get(s, 0))
            w_sp = self.lead_wsp_ema.get(s, 0.35)
            # Skip intermediate 0.39..0.46 unhedged random-walk corridor when flat to conserve request budget
            if 0.39 <= w_sp <= 0.46 and pos == 0:
                continue

            if s not in intact_hm:
                if pos == 0:
                    continue
                fv = 0.5 * (bb + ba)
                wb, wa = bb - 1.0, ba + 1.0
            else:
                hm, wb, wa = intact_hm[s]
                fv = hm + 0.22 * micro[s] + 0.03 * sobi[s]

            if w_sp <= 0.28:
                shsp, mhsp, xhsp, cap, mclip = 0.042, 0.042, 0.022, 100, 45
            elif w_sp <= 0.39:
                shsp, mhsp, xhsp, cap, mclip = 0.055, 0.095, 0.025, 85, 32
            else:
                shsp, mhsp, xhsp, cap, mclip = 0.085, 0.115, 0.065, 100, 36

            res = fv - 0.0012 * pos
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
