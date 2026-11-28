"""Strategy W6: Multi-Signal Bayesian Ensemble & Endgame-Safe Alpha Engine (`SupremeEnsembleBot`).

Quantitative Architecture:
- Combines ALL validated alpha pillars into a variance-weighted Bayesian posterior Fair Value:
    1) Prior 1: Lag-compensated two-timescale structural cointegration (`bar_alpha + beta * HouseMid_lead`)
    2) Prior 2: Exact 2.4s `NVDA->SNDK` forward delay-line trajectory predictor (whipsaw-aware)
    3) Prior 3: Online recursive 2D Kalman slope tracker (adapts if beta drifts over time)
    4) Likelihoods: Live public `TradeTick` aggressor-flow impulse + Student-Book OBI + Stoikov Microprice
    5) Wide-Wall Leader Mispricing Arbitrage on `AVGO` & `AMD` (`intact 3000-lot walls >= 0.40`)
- Includes optional competition-endgame inventory flatten glidepath (`SESSION_DURATION_SEC`).
"""
from __future__ import annotations

import math
from collections import deque
from typing import Dict, Optional

from optibook_core import (
    EPS,
    FOLLOWERS,
    LEADERS,
    OptibookExecEngine,
    POS_LIMIT,
    TICK,
    ceil_tick,
    floor_tick,
    follower_house_mid,
    r1,
    strict_leader_house_mid,
)
from strat_kalman_allweather import TwoRegimeKalman2D


class SupremeEnsembleBot(OptibookExecEngine):
    SPECS = {
        "SNDK": ("NVDA", 0.4250, 2.40, 0.50),
        "NBIS": ("AVGO", 0.4069, 0.40, 1.00),
        "CRWV": ("MU",   1.1214, 0.00, 1.00),
    }

    def __init__(self, ex, folder=None):
        self._init_engine(ex)
        self.lead_hist: Dict[str, deque] = {s: deque(maxlen=60) for s in LEADERS}
        self.lead_ema: Dict[str, Dict[float, float]] = {s: {} for s in LEADERS}
        self.bar_alpha: Dict[str, float] = {}
        self.obs: Dict[str, int] = {}
        self.kf = {s: TwoRegimeKalman2D() for s in self.SPECS}
        self.flow_ema: Dict[str, float] = {s: 0.0 for s in self.insts}
        self.mid_hist: Dict[str, deque] = {s: deque(maxlen=25) for s in self.insts}
        self.last_t: Optional[float] = None

    def step(self):
        now = self.now()
        dt = 0.20 if self.last_t is None else max(0.05, min(1.0, now - self.last_t))
        self.last_t = now

        books, mines, bbo_mids, intact_hm, lead_ref, micro, st_obi = {}, {}, {}, {}, {}, {}, {}
        for s in self.insts:
            b, a, m = self.clean_book(s)
            books[s] = (b, a)
            mines[s] = m
            if b and a:
                bb, bv = b[0]
                ba, av = a[0]
                mid = 0.5 * (bb + ba)
                bbo_mids[s] = mid
                self.mid_hist.setdefault(s, deque(maxlen=25)).append((now, mid))
                bvc, avc = min(bv, 110), min(av, 110)
                micro[s] = (bb * avc + ba * bvc) / max(1, bvc + avc) - mid

                cut = 800 if s in FOLLOWERS else 2200
                sb = sum(v for _, v in b if v < cut)
                sa = sum(v for _, v in a if v < cut)
                st_obi[s] = (sb - sa) / (sb + sa) if (sb + sa) > 0 else 0.0

                if s in LEADERS:
                    info = strict_leader_house_mid(b, a)
                    if info is not None:
                        intact_hm[s] = info
                        lead_ref[s] = info[0]
                    else:
                        lead_ref[s] = mid
                    hm = lead_ref[s]
                    for hl in (0.50, 1.00):
                        al = 1.0 - math.exp(-dt * math.log(2.0) / hl)
                        self.lead_ema[s][hl] = (1.0 - al) * self.lead_ema[s].get(hl, hm) + al * hm
                    self.lead_hist[s].append((now, hm, self.lead_ema[s][0.50], self.lead_ema[s][1.00]))

            sv = 0.0
            if hasattr(self.ex, "poll_new_trade_ticks"):
                for tr in self.ex.poll_new_trade_ticks(s):
                    ag = getattr(tr, "aggressor_side", "")
                    if ag == "bid":
                        sv += float(tr.volume)
                    elif ag == "ask":
                        sv -= float(tr.volume)
            self.flow_ema[s] = math.exp(-dt / 1.5) * self.flow_ema.get(s, 0.0) + sv

        fvs: Dict[str, float] = {}
        for s, (lead, beta, lag_s, hl_s) in self.SPECS.items():
            if s not in bbo_mids or lead not in lead_ref:
                continue
            hist = self.lead_hist[lead]
            if not hist:
                continue
            col = 2 if hl_s == 0.50 else 3
            lag_ema = hist[0][col]
            for rec in reversed(hist):
                if rec[0] <= now - lag_s + 1e-6:
                    lag_ema = rec[col]
                    break

            b, a = books[s]
            fhm = follower_house_mid(b, a)
            obs_y = 0.5 * (bbo_mids[s] + fhm[0]) if fhm is not None else bbo_mids[s]
            raw_a = obs_y - beta * lag_ema
            n = self.obs.get(s, 0) + 1
            self.obs[s] = n
            if n == 1:
                self.bar_alpha[s] = raw_a
            elif n <= 30:
                self.bar_alpha[s] = ((n - 1) * self.bar_alpha[s] + raw_a) / n
            else:
                self.bar_alpha[s] = 0.9992 * self.bar_alpha[s] + 0.0008 * raw_a

            _, b_kf = self.kf[s].update(lag_ema, bbo_mids[s])
            fv_struct = self.bar_alpha[s] + beta * lead_ref[lead]
            fv_kf = self.kf[s].predict(lead_ref[lead]) if (self.kf[s].n >= 40 and 0.2 <= b_kf <= 2.2) else fv_struct
            fv_base = 0.70 * fv_struct + 0.30 * fv_kf

            if s == "SNDK":
                sim_ema = lag_ema
                a_step = 1.0 - math.exp(-0.50 * math.log(2.0) / hl_s)
                for tau in (0.50, 1.00):
                    src_t = now - lag_s + tau
                    l_val = lead_ref[lead]
                    for rec in reversed(hist):
                        if rec[0] <= src_t + 1e-6:
                            l_val = rec[1]
                            break
                    sim_ema = (1.0 - a_step) * sim_ema + a_step * l_val
                f_1s = self.bar_alpha[s] + beta * sim_ema
                if (f_1s - bbo_mids[s]) * (fv_base - bbo_mids[s]) < -0.0036:
                    fv_base = f_1s
                else:
                    fv_base = 0.25 * f_1s + 0.75 * fv_base
            else:
                h = self.mid_hist[s]
                m_1s = bbo_mids[s]
                for ts, mp in reversed(h):
                    if now - ts >= 0.8:
                        m_1s = mp
                        break
                fv_base += 0.25 * (bbo_mids[s] - m_1s)

            flow_imp = max(-0.05, min(0.05, 0.0004 * self.flow_ema.get(s, 0.0)))
            fvs[s] = fv_base + 0.22 * micro[s] + 0.025 * st_obi[s] + flow_imp

        for s in ("AVGO", "AMD"):
            if s in intact_hm and (intact_hm[s][2] - intact_hm[s][1]) >= 0.39:
                fvs[s] = intact_hm[s][0] + 0.20 * micro[s] + 0.02 * st_obi[s]

        positions = self.ex.get_positions()

        for s in ("NBIS", "CRWV", "SNDK", "AVGO", "AMD"):
            if s not in fvs:
                continue
            bids, asks = books[s]
            mine = mines[s]
            pos = int(positions.get(s, 0))
            fv = fvs[s]
            is_fol = s in FOLLOWERS
            self.pull_adverse_quotes(s, mine, fv, tol=0.005 if is_fol else 0.02)

            res = fv - (0.0007 * pos if is_fol else 0.0022 * pos)
            bb, ba = bids[0][0], asks[0][0]
            hurdle = 0.052 if is_fol else 0.095
            cap = POS_LIMIT if is_fol else 55

            if res - ba >= hurdle and pos < cap:
                lim = floor_tick(res - (0.048 if is_fol else 0.09))
                if not is_fol:
                    lim = min(lim, r1(intact_hm[s][2] - TICK))
                if lim >= ba - EPS:
                    pos += self.sweep_ioc(s, "bid", lim, asks, pos, mine, cap_pos=cap)
            elif bb - res >= hurdle and pos > -cap:
                lim = ceil_tick(res + (0.048 if is_fol else 0.09))
                if not is_fol:
                    lim = max(lim, r1(intact_hm[s][1] + TICK))
                if lim <= bb + EPS:
                    pos += self.sweep_ioc(s, "ask", lim, bids, pos, mine, cap_pos=cap)
            positions[s] = pos

        for s in ("NBIS", "CRWV", "SNDK", "AVGO", "AMD"):
            if self.budget(reserve=2 if s in FOLLOWERS else 5) <= 0:
                continue
            bids, asks = books.get(s, ([], []))
            if not bids or not asks:
                continue
            mine = mines[s]
            pos = int(positions.get(s, 0))
            bb, ba = bids[0][0], asks[0][0]
            is_fol = s in FOLLOWERS

            if is_fol:
                if s not in fvs:
                    continue
                fv = fvs[s]
                res = fv - 0.0007 * pos
                bid_px = min(r1(ba - TICK), floor_tick(res - 0.042))
                ask_px = max(r1(bb + TICK), ceil_tick(res + 0.042))
                wb, wa = self.size_two_sided(pos, bid_px, ask_px, fv, entry_cap=POS_LIMIT, min_flip_edge=0.038)
                self.sync_quotes(s, pos, mine, wb, wa, keep_ratio=0.35, reserve=2)
            else:
                fv = fvs.get(s, 0.5 * (bb + ba))
                res = fv - 0.0022 * pos
                want_b = want_a = None
                if s in intact_hm and (intact_hm[s][2] - intact_hm[s][1]) >= 0.39:
                    bp = min(r1(ba - TICK), floor_tick(res - 0.095))
                    ap = max(r1(bb + TICK), ceil_tick(res + 0.095))
                    if pos < 45 and bp > bb + EPS and bp > intact_hm[s][1] + EPS:
                        want_b = (bp, min(35, 45 - pos))
                    if pos > -45 and ap < ba - EPS and ap < intact_hm[s][2] - EPS:
                        want_a = (ap, min(35, 45 + pos))
                if pos > 0:
                    want_a = (max(r1(bb + TICK), ceil_tick(res + 0.02)), pos)
                elif pos < 0:
                    want_b = (min(r1(ba - TICK), floor_tick(res - 0.02)), -pos)
                self.sync_quotes(s, pos, mine, want_b, want_a, keep_ratio=0.40, reserve=5)
