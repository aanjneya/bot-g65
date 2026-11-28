"""Architecture 7: All-Regime Pair-Factor GLFT + Online Jump-RLS + Leader Gap-Shield (`ApexAllWeatherBot`).

Empirically engineered from both the 24 overnight chunks (606 min) AND the 21 live Saturday afternoon
rounds (63 min, Regime 1 tight-spread + Regime 2 wide/crash-shock):
1) Block-Diagonal 2x2 Pair-Factor GLFT Core (`BlockPairGLFTBot`):
   - Cross-hedging factor inventory `Q_fac = q_lead + beta_t * q_fol` + cubic tail skew + `AMD` excluded.
   - `SNDK` convex 2.40s delay-line wave envelope + `keep_touch_hyst=True` queue preservation.
2) Live Centered Jump-RLS Beta Adaptation + Fast Innovation-Gated `alpha_c` Reset (`BlindGraphKalmanBot`):
   - Adapts live when Optiver shifts pair betas (e.g. `SNDK/NVDA: 1.20->0.35`, `CRWV/MU: 1.56->0.90`).
   - If `|u_OU| >= 0.65` persists for `>= 6.0s` without reverting (regime step-change), boosts `ALPHA_RATE`
     `8x` so the bot never stays max-pinned fighting a stale fair value.
3) Leader Gap-Jump Shield (`K1` + `MultiAssetGLFT` rule):
   - Sweeps mispriced leaders via safe IOCs when walls are tight (`wa - wb < 0.30`) and ONLY rests passive
     leader quotes when intact house walls are wide (`wa - wb >= 0.30`) with capped clips — eliminating
     resting leader adverse-selection blowups during atomic 50ms `$5-$38` leader teleports.
4) Live Microstructure Overlay (`StudentBookMicroPennyBot`):
   - Distance-weighted Student OBI + volume-capped Stoikov Microprice + 1.5s OFI + live `TradeTick` flow.
5) Safe Competition Endgame Glidepath:
   - Activates ONLY when `SESSION_DURATION_SEC >= 600` (e.g. 1800s Sunday final), gliding inventory smoothly
     to 0 in the final 75s without distorting short round-robin tests.
"""
from __future__ import annotations

import math
import os
from collections import deque
from typing import Dict, List, Optional, Tuple

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


def _weighted_student_obi(inst: str, bids: List[Tuple[float, int]], asks: List[Tuple[float, int]], mid: float) -> float:
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


class AdaptiveJumpSlope:
    """Centered jump-gated RLS slope estimator with gentle forgetting for live beta shifts."""

    __slots__ = ("x0", "sxx", "sxy")

    def __init__(self, x0: float, b0: float, prior_strength: float = 12.0):
        self.x0 = x0
        self.sxx = prior_strength
        self.sxy = prior_strength * b0

    def update(self, dx: float, dy: float):
        if abs(dx) >= 0.35:
            # Cap single outlier jump weight so one anomalous print cannot swing beta wildly
            w_dx = max(-6.0, min(6.0, dx))
            scale = w_dx / dx
            w_dy = dy * scale
            self.sxx = 0.995 * self.sxx + w_dx * w_dx
            self.sxy = 0.995 * self.sxy + w_dx * w_dy

    @property
    def beta(self) -> float:
        return max(0.15, min(2.00, self.sxy / max(1e-6, self.sxx)))


class ApexAllWeatherBot(SmartOptibookEngine):
    BASE_CLIP = 50
    EDGE_SCALE = 200.0
    PASSIVE_CAP = 84
    ENTRY_HSP = 0.038
    EXIT_HSP = 0.034
    GAMMA_ID = 0.00034
    GAMMA_CUBIC = 0.018
    GAMMA_FAC = 0.00030
    ALPHA_RATE = 0.0015
    ALPHA_FAST_RATE = 0.0120
    OU_SHOCK_THRESH = 0.65
    OU_SHOCK_PERSIST_SEC = 6.0
    LEADER_PASSIVE_MIN_WALL_SPREAD = 0.30

    W_OU = {"SNDK": 1.00, "NBIS": 0.85, "CRWV": 0.60}
    MOM_WT = {"SNDK": 0.00, "NBIS": 0.28, "CRWV": 0.28}

    # Initial priors (automatically adapted online via AdaptiveJumpSlope)
    SPECS = {
        "SNDK": ("NVDA", 0.4248, 2.40, 0.50),
        "NBIS": ("AVGO", 0.4065, 0.40, 1.00),
        "CRWV": ("MU",   1.1180, 0.00, 1.00),
    }
    LEAD_TO_FOL = {"NVDA": ("SNDK", 0.4248), "AVGO": ("NBIS", 0.4065), "MU": ("CRWV", 1.1180)}

    # (sweep_hsp, make_hsp, exit_hsp, pos_cap, make_clip) — AMD excluded
    LEAD_CFG = {
        "AVGO": (0.085, 0.115, 0.060, 80, 30),
        "NVDA": (0.055, 0.095, 0.025, 76, 30),
        "VRT":  (0.042, 0.045, 0.022, 84, 38),
        "MU":   (0.045, 0.050, 0.024, 80, 36),
    }

    def __init__(self, ex, folder=None):
        self._init_core(ex)
        self.slope_est: Dict[str, AdaptiveJumpSlope] = {}
        self.alpha_c: Dict[str, float] = {}
        self.obs: Dict[str, int] = {}
        self.last_rls_t: Dict[str, float] = {}
        self.ou_shock_since: Dict[str, Optional[float]] = {}
        self.prev_bbo: Dict[str, Tuple[float, int, float, int]] = {}
        self.ofi_ema: Dict[str, float] = {s: 0.0 for s in self.insts}
        self.flow_ema: Dict[str, float] = {s: 0.0 for s in self.insts}
        self.mid_hist: Dict[str, deque] = {s: deque(maxlen=400) for s in self.insts}
        self.fhm_hist: Dict[str, deque] = {s: deque(maxlen=400) for s in FOLLOWERS}
        self.lead_hist: Dict[str, deque] = {s: deque(maxlen=700) for s in LEADERS}
        self.lead_ema: Dict[str, Dict[float, float]] = {s: {} for s in LEADERS}
        self.t_start: Optional[float] = None
        raw_sess = float(os.environ.get("SESSION_DURATION_SEC", "0") or 0.0)
        # Only arm the final-75s endgame flattener for real long sessions (>=10 min)
        self.session_sec = raw_sess if raw_sess >= 600.0 else 0.0
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
        if self.session_sec > 0.0:
            rem = self.session_sec - (now - self.t_start)
            if rem <= 75.0:
                cap_scale = max(0.0, (rem - 12.0) / 63.0)
                endgame_skew_mult = 3.0

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

            if not b or not a:
                continue
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
                a_ofi = 1.0 - math.exp(-dt / 1.0)
                self.ofi_ema[s] = (1.0 - a_ofi) * self.ofi_ema.get(s, 0.0) + a_ofi * float(db - da)
            self.prev_bbo[s] = (bb, bv, ba, av)

            micro[s] = (bb * av + ba * bv) / max(1, bv + av) - mid
            wobi[s] = _weighted_student_obi(s, b, a, mid)
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
                self.slope_est[s] = AdaptiveJumpSlope(lag_ema, b0, prior_strength=12.0)
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
                u_ou = 0.0
            elif n <= 20:
                self.alpha_c[s] = ((n - 1) * self.alpha_c[s] + raw_ac) / n
                u_ou = (self.alpha_c[s] + beta * (lag_ema - est.x0)) - cur_m
            else:
                u_pre = (self.alpha_c[s] + beta * (lag_ema - est.x0)) - cur_m
                if abs(u_pre) >= self.OU_SHOCK_THRESH:
                    if self.ou_shock_since.get(s) is None:
                        self.ou_shock_since[s] = now
                else:
                    self.ou_shock_since[s] = None

                shock_t0 = self.ou_shock_since.get(s)
                use_fast = shock_t0 is not None and (now - shock_t0) >= self.OU_SHOCK_PERSIST_SEC
                base_rate = self.ALPHA_FAST_RATE if use_fast else self.ALPHA_RATE
                r = 1.0 - (1.0 - base_rate) ** (dt / 0.20)
                self.alpha_c[s] = (1.0 - r) * self.alpha_c[s] + r * raw_ac
                u_ou = (self.alpha_c[s] + beta * (lag_ema - est.x0)) - cur_m

            f_term = cur_m + self.W_OU[s] * u_ou + beta * (lead_px[lead] - lag_ema)

            mom_term = 0.0
            if self.MOM_WT[s] != 0.0:
                m_1s = self._lookup_series(self.mid_hist[s], now - 0.8) or cur_m
                mom_term = self.MOM_WT[s] * (cur_m - m_1s)

            adj = mom_term + 0.28 * micro[s] + 0.055 * wobi[s] + ofi_sig[s] + tape_sig[s]

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
                    bv = min(bv, MAX_OPEN_VOL)
                    av = max(0, min(av, MAX_OPEN_VOL - bv))
                elif cross_a > cross_b or pos > 0:
                    av = min(av, MAX_OPEN_VOL)
                    bv = max(0, min(bv, MAX_OPEN_VOL - av))
                else:
                    bv = min(bv, 100)
                    av = min(av, 100)

            urg = max(cross_b * max(0.01, fv_b - ba), cross_a * max(0.01, bb - fv_a))
            fol_plan.append((urg, s, fv, bids, asks, (bp, bv) if bv > 0 else None, (ap, av) if av > 0 else None))

        fol_plan.sort(key=lambda x: x[0], reverse=True)
        for _, s, fv, bids, asks, wb, wa in fol_plan:
            self.execute_quote_pair(s, fv, bids, asks, wb, wa, "limit", "limit", reserve=0, keep_touch_hyst=True)

        # Leaders with Gap-Jump Shield: sweep via IOC when walls are tight, only rest passive inside wide walls
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
                allow_passive_make = False
            else:
                hm, wb, wa = intact_hm[s]
                fv = hm + 0.22 * micro[s] + 0.04 * wobi[s] + 0.5 * ofi_sig[s]
                allow_passive_make = (wa - wb) >= self.LEADER_PASSIVE_MIN_WALL_SPREAD - EPS

            fac_adj = 0.0
            if s in self.LEAD_TO_FOL:
                fol, b0 = self.LEAD_TO_FOL[s]
                b_live = self.slope_est[fol].beta if fol in self.slope_est else b0
                q_fac = pos + b_live * int(positions.get(fol, 0))
                fac_adj = max(-0.024, min(0.024, self.GAMMA_FAC * q_fac))

            u_l = pos / 100.0
            res = fv - (0.0012 * pos + 0.012 * (u_l * u_l * u_l) + fac_adj) * endgame_skew_mult
            sb_px = min(r1(wa - TICK), floor_tick(res - shsp))
            sa_px = max(r1(wb + TICK), ceil_tick(res + shsp))
            cross_b = sum(v for p, v in asks if p <= sb_px + EPS and p < wa - EPS) if s in intact_hm else 0
            cross_a = sum(v for p, v in bids if p >= sa_px - EPS and p > wb + EPS) if s in intact_hm else 0

            want_b = want_a = None
            ot_b = ot_a = "limit"

            if cross_b > 0 and pos < cap:
                if allow_passive_make:
                    want_b = (sb_px, min(cap - pos, cross_b + mclip))
                else:
                    want_b = (sb_px, min(cap - pos, cross_b))
                    ot_b = "ioc"
            elif allow_passive_make and pos < cap:
                mb_px = min(r1(wa - TICK), floor_tick(res - mhsp), r1(bb + TICK))
                if mb_px > wb + EPS and mb_px > bb + EPS:
                    want_b = (mb_px, min(mclip, cap - pos))

            if cross_a > 0 and pos > -cap:
                if allow_passive_make:
                    want_a = (sa_px, min(cap + pos, cross_a + mclip))
                else:
                    want_a = (sa_px, min(cap + pos, cross_a))
                    ot_a = "ioc"
            elif allow_passive_make and pos > -cap:
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
