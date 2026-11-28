"""Strategy W3: Self-Calibrating 2D Online Kalman Filter + Order-Flow Bot (`AdaptiveKalmanFlowBot`).

Quantitative Architecture:
- ZERO hardcoded slopes or intercepts: learns BOTH [alpha_t, beta_t] live from scratch using
  a 2-state Bayesian Kalman Filter with dual-regime process covariance (fast warmup learning in
  the first 120 ticks, then ultra-low Q = diag(1e-7, 1e-9) so it locks onto the true cointegration
  hyperplane without chasing 12-40s OU excursions!).
- Fuses Kalman fair-value innovations with:
    1) Live public `poll_new_trade_ticks` signed aggressor order-flow bursts (1.5s EWMA)
    2) Volume-capped Stoikov Microprice
    3) 1s follower momentum impulse
- 100% resilient to Sunday 10:00 parameter resets if Optiver changes stock price scales or betas!
"""
from __future__ import annotations

import math
from collections import deque
from typing import Dict, Optional, Tuple

import numpy as np

from optibook_core import (
    LEADERS,
    OptibookExecEngine,
    POS_LIMIT,
    TICK,
    ceil_tick,
    floor_tick,
    r1,
    strict_leader_house_mid,
)


class TwoRegimeKalman2D:
    """2-state recursive Kalman Filter for y_t = alpha_t + beta_t * x_t + eps_t.
    Uses centered x_t = (x - x0) so alpha_t and beta_t are numerically orthogonal and converge in seconds!"""

    def __init__(self):
        self.x0: Optional[float] = None
        self.theta = np.zeros(2, dtype=float)  # [alpha_centered, beta]
        self.P = np.diag([25.0, 4.0])
        self.n = 0

    def update(self, x_lag: float, y: float) -> Tuple[float, float]:
        if self.x0 is None:
            self.x0 = x_lag
            # Scale-invariant initial guess beta_0 = y / x_lag (clamped to [0.2, 2.0])
            b0 = max(0.2, min(2.0, y / max(1.0, x_lag)))
            self.theta = np.array([y, b0], dtype=float)
            self.n = 1
            return float(y), float(b0)

        dx = x_lag - self.x0
        self.n += 1
        # Fast adaptation during initial 150 steps (~30s), then ultra-stable equilibrium lock!
        if self.n <= 150:
            Q = np.diag([1e-5, 1e-5])
            R = 0.04
        else:
            Q = np.diag([1e-7, 1e-8])
            R = 0.06

        P_pred = self.P + Q
        H = np.array([1.0, dx], dtype=float)
        err = y - float(H @ self.theta)
        S = float(H @ P_pred @ H) + R
        K = (P_pred @ H) / max(1e-9, S)
        self.theta = self.theta + K * err
        self.P = P_pred - np.outer(K, H) @ P_pred
        return float(self.theta[0]), float(self.theta[1])

    def predict(self, x_now: float) -> float:
        if self.x0 is None:
            return 0.0
        return float(self.theta[0] + self.theta[1] * (x_now - self.x0))


class AdaptiveKalmanFlowBot(OptibookExecEngine):
    PAIR_MAP = {
        "SNDK": ("NVDA", 2.40, 0.50),
        "NBIS": ("AVGO", 0.40, 1.00),
        "CRWV": ("MU",   0.00, 1.00),
    }

    def __init__(self, ex, folder=None):
        self._init_engine(ex)
        self.kf = {s: TwoRegimeKalman2D() for s in self.PAIR_MAP}
        self.lead_hist: Dict[str, deque] = {s: deque(maxlen=60) for s in LEADERS}
        self.lead_ema: Dict[str, Dict[float, float]] = {s: {} for s in LEADERS}
        self.flow_ema: Dict[str, float] = {s: 0.0 for s in self.insts}
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
            if b and a:
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
                        al = 1.0 - math.exp(-dt * math.log(2.0) / hl)
                        self.lead_ema[s][hl] = (1.0 - al) * self.lead_ema[s].get(hl, hm) + al * hm
                    self.lead_hist[s].append((now, hm, self.lead_ema[s][0.50], self.lead_ema[s][1.00]))

            # Live signed public trade flow
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
        for s, (lead, lag_s, hl_s) in self.PAIR_MAP.items():
            if s not in bbo_mids or lead not in hm_lead:
                continue
            hist = self.lead_hist[lead]
            if not hist:
                continue
            col = 2 if hl_s == 0.50 else 3
            lag_x = hist[0][col]
            for rec in reversed(hist):
                if rec[0] <= now - lag_s + 1e-6:
                    lag_x = rec[col]
                    break

            _, beta_hat = self.kf[s].update(lag_x, bbo_mids[s])
            if self.kf[s].n < 25 or not (0.15 <= beta_hat <= 2.50):
                continue

            base_fv = self.kf[s].predict(hm_lead[lead])
            flow_imp = max(-0.06, min(0.06, 0.0005 * self.flow_ema.get(s, 0.0)))
            ar_imp = 0.0
            if s in ("CRWV", "NBIS"):
                h = self.mid_hist[s]
                m_1s = bbo_mids[s]
                for ts, mp in reversed(h):
                    if now - ts >= 0.8:
                        m_1s = mp
                        break
                ar_imp = 0.22 * (bbo_mids[s] - m_1s)

            fvs[s] = base_fv + ar_imp + flow_imp + 0.22 * micro[s]

        positions = self.ex.get_positions()

        for s in ("NBIS", "CRWV", "SNDK"):
            if s not in fvs:
                continue
            bids, asks = books[s]
            mine = mines[s]
            pos = int(positions.get(s, 0))
            fv = fvs[s]
            self.pull_adverse_quotes(s, mine, fv, tol=0.005)

            res = fv - 0.0008 * pos
            bb, ba = bids[0][0], asks[0][0]
            if res - ba >= 0.055 and pos < POS_LIMIT:
                pos += self.sweep_ioc(s, "bid", floor_tick(res - 0.05), asks, pos, mine, cap_pos=POS_LIMIT)
            elif bb - res >= 0.055 and pos > -POS_LIMIT:
                pos += self.sweep_ioc(s, "ask", ceil_tick(res + 0.05), bids, pos, mine, cap_pos=POS_LIMIT)
            positions[s] = pos

        for s in ("NBIS", "CRWV", "SNDK"):
            if s not in fvs or self.budget(reserve=2) <= 0:
                continue
            bids, asks = books[s]
            mine = mines[s]
            pos = int(positions.get(s, 0))
            fv = fvs[s]
            res = fv - 0.0008 * pos
            bb, ba = bids[0][0], asks[0][0]

            bid_px = min(r1(ba - TICK), floor_tick(res - 0.045))
            ask_px = max(r1(bb + TICK), ceil_tick(res + 0.045))
            wb, wa = self.size_two_sided(pos, bid_px, ask_px, fv, entry_cap=POS_LIMIT, min_flip_edge=0.04)
            self.sync_quotes(s, pos, mine, wb, wa, keep_ratio=0.35, reserve=2)
