"""Strategy W2: Exact Delay-Buffer & Forward-Trajectory Arbitrageur (`DelayBufferTrajectoryBot`).

Quantitative Architecture:
- Signal-processing deconvolution of Optiver's exact delay-line + EMA filter:
    * SNDK <- NVDA: 2.40s ring buffer + 0.50s EMA (beta=0.425)
    * NBIS <- AVGO: 0.40s ring buffer + 1.00s EMA (beta=0.407)
    * CRWV <- MU  : 0.00s ring buffer + 1.00s EMA (beta=1.121)
- Crucially separates the SLOW equilibrium intercept bar_alpha_i (calibrated causally against
  the lag-matched EMA of the leader so jumps create ZERO lag bias!) from the FAST forward
  trajectory {F_hat(t + tau)} simulated through the 2.4s leader history buffer!
- Detects whipsaw reversals inside SNDK's 2.4s lookahead window (trading the 0.8s-1.2s wave
  first when it opposes the 2.4s terminal wave!).
"""
from __future__ import annotations

import math
from collections import deque
from typing import Dict, List, Optional

from optibook_core import (
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


class DelayBufferTrajectoryBot(OptibookExecEngine):
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
        self.mid_hist: Dict[str, deque] = {s: deque(maxlen=25) for s in self.insts}
        self.last_t: Optional[float] = None

    def step(self):
        now = self.now()
        dt = 0.20 if self.last_t is None else max(0.05, min(1.0, now - self.last_t))
        self.last_t = now

        books, mines, bbo_mids, hm_lead, micro = {}, {}, {}, {}, {}
        for s in self.insts:
            b, a, m = self.clean_book(s)
            books[s] = (b, a)
            mines[s] = m
            if not b or not a:
                continue
            bb, bv = b[0]
            ba, av = a[0]
            mid = 0.5 * (bb + ba)
            bbo_mids[s] = mid
            self.mid_hist.setdefault(s, deque(maxlen=25)).append((now, mid))

            bvc, avc = min(bv, 120), min(av, 120)
            micro[s] = (bb * avc + ba * bvc) / max(1, bvc + avc) - mid

            if s in LEADERS:
                info = strict_leader_house_mid(b, a)
                hm = info[0] if info is not None else mid
                hm_lead[s] = hm
                for hl in (0.50, 1.00):
                    alpha = 1.0 - math.exp(-dt * math.log(2.0) / hl)
                    self.lead_ema[s][hl] = (1.0 - alpha) * self.lead_ema[s].get(hl, hm) + alpha * hm
                self.lead_hist[s].append((now, hm, self.lead_ema[s][0.50], self.lead_ema[s][1.00]))

        f_active: Dict[str, float] = {}
        for s, (lead, beta, lag_s, hl_s) in self.SPECS.items():
            if s not in bbo_mids or lead not in hm_lead:
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

            # Calibrate bar_alpha against lag-matched EMA with ULTRA-SLOW rate (0.0008 -> 4.2 min HL!)
            # so OU deviations of follower are preserved 100% while leader jumps cause zero bias!
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

            alp = self.bar_alpha[s]
            f_term = alp + beta * hm_lead[lead]

            if s == "SNDK":
                # Simulate exact 1.0s-ahead point from NVDA at t - 1.4s through 0.5s EMA!
                sim_ema = lag_ema
                a_step = 1.0 - math.exp(-0.50 * math.log(2.0) / hl_s)
                for tau in (0.50, 1.00):
                    src_t = now - lag_s + tau
                    l_val = hm_lead[lead]
                    for rec in reversed(hist):
                        if rec[0] <= src_t + 1e-6:
                            l_val = rec[1]
                            break
                    sim_ema = (1.0 - a_step) * sim_ema + a_step * l_val
                f_1s = alp + beta * sim_ema
                cur_m = bbo_mids[s]
                # If 1.0s wave and 2.4s terminal wave oppose each other by > 0.06, ride 1.0s wave first!
                if (f_1s - cur_m) * (f_term - cur_m) < -0.0036:
                    fv = f_1s + 0.20 * micro[s]
                else:
                    fv = 0.30 * f_1s + 0.70 * f_term + 0.20 * micro[s]
            else:
                h = self.mid_hist[s]
                m_1s = bbo_mids[s]
                for ts, mp in reversed(h):
                    if now - ts >= 0.8:
                        m_1s = mp
                        break
                fv = f_term + 0.24 * (bbo_mids[s] - m_1s) + 0.22 * micro[s]

            f_active[s] = fv

        positions = self.ex.get_positions()

        for s in ("SNDK", "NBIS", "CRWV"):
            if s not in f_active:
                continue
            bids, asks = books[s]
            mine = mines[s]
            pos = int(positions.get(s, 0))
            fv = f_active[s]
            self.pull_adverse_quotes(s, mine, fv, tol=0.005)

            res = fv - 0.00075 * pos
            bb, ba = bids[0][0], asks[0][0]
            if res - ba >= 0.055 and pos < POS_LIMIT:
                pos += self.sweep_ioc(s, "bid", floor_tick(res - 0.05), asks, pos, mine, cap_pos=POS_LIMIT)
            elif bb - res >= 0.055 and pos > -POS_LIMIT:
                pos += self.sweep_ioc(s, "ask", ceil_tick(res + 0.05), bids, pos, mine, cap_pos=POS_LIMIT)
            positions[s] = pos

        for s in ("SNDK", "NBIS", "CRWV"):
            if s not in f_active or self.budget(reserve=2) <= 0:
                continue
            bids, asks = books[s]
            mine = mines[s]
            pos = int(positions.get(s, 0))
            fv = f_active[s]
            res = fv - 0.00075 * pos
            bb, ba = bids[0][0], asks[0][0]

            bid_px = min(r1(ba - TICK), floor_tick(res - 0.045))
            ask_px = max(r1(bb + TICK), ceil_tick(res + 0.045))
            wb, wa = self.size_two_sided(pos, bid_px, ask_px, fv, entry_cap=POS_LIMIT, min_flip_edge=0.04)
            self.sync_quotes(s, pos, mine, wb, wa, keep_ratio=0.35, reserve=2)
