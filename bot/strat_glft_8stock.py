"""Strategy W4: All-8-Stock Multi-Asset GLFT Structural Portfolio Market Maker (`MultiAssetGLFTPortfolioBot`).

Quantitative Architecture:
- Trades ALL 8 INSTRUMENTS (`AMD`, `AVGO`, `CRWV`, `MU`, `NBIS`, `NVDA`, `SNDK`, `VRT`) as a unified
  multi-asset market-making portfolio!
- On the 5 leaders (`AVGO`, `AMD`, `NVDA`, `MU`, `VRT`): ONLY trusts `LivelyLasagnas`'s house midpoint
  when BOTH symmetric 3000-lot walls (`v >= 2400`) are intact and wide (`wall_spread >= 0.40`).
  Snipes blatant student mispricings crossing intact `HouseMid +- 0.08` and quotes `queue = 0`
  strictly inside wide BBO spreads (`>= 0.20`).
- On the 3 followers (`SNDK`, `NBIS`, `CRWV`): combines lag-compensated slow equilibrium cointegration
  with non-linear GLFT inventory penalty `gamma * sign(q) * |q|^1.25` and cross-portfolio net-delta skew!
"""
from __future__ import annotations

import math
from collections import deque
from typing import Dict, Optional

from optibook_core import (
    ALL_STOCKS,
    EPS,
    FOLLOWERS,
    LEADERS,
    OptibookExecEngine,
    POS_LIMIT,
    TICK,
    ceil_tick,
    floor_tick,
    r1,
    strict_leader_house_mid,
)


class MultiAssetGLFTPortfolioBot(OptibookExecEngine):
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
        self.mid_hist: Dict[str, deque] = {s: deque(maxlen=25) for s in ALL_STOCKS}
        self.last_t: Optional[float] = None

    def _glft_skew(self, s: str, pos: int, net_port_pos: int) -> float:
        if pos == 0 and net_port_pos == 0:
            return 0.0
        g = 0.00038 if s in FOLLOWERS else 0.00180
        own_term = g * math.copysign(abs(pos) ** 1.25, pos)
        port_term = 0.00005 * net_port_pos
        return own_term + port_term

    def step(self):
        now = self.now()
        dt = 0.20 if self.last_t is None else max(0.05, min(1.0, now - self.last_t))
        self.last_t = now

        books, mines, bbo_mids, intact_hm, lead_ref, micro = {}, {}, {}, {}, {}, {}
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
            self.mid_hist[s].append((now, mid))

            bvc, avc = min(bv, 120), min(av, 120)
            micro[s] = (bb * avc + ba * bvc) / max(1, bvc + avc) - mid

            if s in LEADERS:
                info = strict_leader_house_mid(b, a)
                if info is not None:
                    intact_hm[s] = info  # (hm, wb, wa)
                    lead_ref[s] = info[0]
                else:
                    lead_ref[s] = mid

                hm = lead_ref[s]
                for hl in (0.50, 1.00):
                    al = 1.0 - math.exp(-dt * math.log(2.0) / hl)
                    self.lead_ema[s][hl] = (1.0 - al) * self.lead_ema[s].get(hl, hm) + al * hm
                self.lead_hist[s].append((now, hm, self.lead_ema[s][0.50], self.lead_ema[s][1.00]))

        fvs: Dict[str, float] = {}
        # 1) Followers FV
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
            raw_a = bbo_mids[s] - beta * lag_ema
            n = self.obs.get(s, 0) + 1
            self.obs[s] = n
            if n == 1:
                self.bar_alpha[s] = raw_a
            elif n <= 30:
                self.bar_alpha[s] = ((n - 1) * self.bar_alpha[s] + raw_a) / n
            else:
                self.bar_alpha[s] = 0.9990 * self.bar_alpha[s] + 0.0010 * raw_a

            target = self.bar_alpha[s] + beta * lead_ref[lead]
            ar_imp = 0.0
            if s in ("CRWV", "NBIS"):
                h = self.mid_hist[s]
                m_1s = bbo_mids[s]
                for ts, mp in reversed(h):
                    if now - ts >= 0.8:
                        m_1s = mp
                        break
                ar_imp = 0.24 * (bbo_mids[s] - m_1s)
            fvs[s] = target + ar_imp + 0.22 * micro[s]

        # 2) Leaders FV (ONLY when strict 3000-lot symmetric walls are intact!)
        for s in LEADERS:
            if s in intact_hm:
                hm, wb, wa = intact_hm[s]
                fvs[s] = hm + 0.18 * micro[s]

        positions = self.ex.get_positions()
        net_port = sum(int(positions.get(k, 0)) for k in self.insts)

        # Pass 1: Followers + Intact Wide-Wall Leaders IOC Mispricing Sweeps
        for s in ("NBIS", "CRWV", "SNDK", "AVGO", "AMD", "NVDA", "MU", "VRT"):
            if s not in fvs:
                continue
            bids, asks = books[s]
            mine = mines[s]
            pos = int(positions.get(s, 0))
            fv = fvs[s]
            is_fol = s in FOLLOWERS
            self.pull_adverse_quotes(s, mine, fv, tol=0.005 if is_fol else 0.02)

            if not is_fol:
                # Require wide intact house wall (>= 0.40) to snipe student quotes on leaders!
                hm, wb, wa = intact_hm[s]
                if wa - wb < 0.39:
                    continue

            res = fv - self._glft_skew(s, pos, net_port)
            bb, ba = bids[0][0], asks[0][0]
            hurdle = 0.055 if is_fol else 0.095
            cap = POS_LIMIT if is_fol else 50

            if res - ba >= hurdle and pos < cap:
                lim = floor_tick(res - (0.05 if is_fol else 0.09))
                # Never sweep into the 3000-lot house wall itself!
                if not is_fol:
                    lim = min(lim, r1(intact_hm[s][2] - TICK))
                if lim >= ba - EPS:
                    d = self.sweep_ioc(s, "bid", lim, asks, pos, mine, cap_pos=cap)
                    pos += d
                    net_port += d
            elif bb - res >= hurdle and pos > -cap:
                lim = ceil_tick(res + (0.05 if is_fol else 0.09))
                if not is_fol:
                    lim = max(lim, r1(intact_hm[s][1] + TICK))
                if lim <= bb + EPS:
                    d = self.sweep_ioc(s, "ask", lim, bids, pos, mine, cap_pos=cap)
                    pos += d
                    net_port += d
            positions[s] = pos

        # Pass 2: Passive GLFT Quoting across Followers (priority) + Leaders (queue=0 inside-spread + exits)
        for s in ("NBIS", "CRWV", "SNDK", "AVGO", "AMD", "NVDA", "MU", "VRT"):
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
                res = fv - self._glft_skew(s, pos, net_port)
                bid_px = min(r1(ba - TICK), floor_tick(res - 0.045))
                ask_px = max(r1(bb + TICK), ceil_tick(res + 0.045))
                wb, wa = self.size_two_sided(pos, bid_px, ask_px, fv, entry_cap=POS_LIMIT, min_flip_edge=0.04)
                self.sync_quotes(s, pos, mine, wb, wa, keep_ratio=0.35, reserve=2)
            else:
                fv = fvs.get(s, 0.5 * (bb + ba))
                res = fv - self._glft_skew(s, pos, net_port)
                want_b = want_a = None
                # Only enter passively on leaders if stepping strictly inside BBO (queue == 0) with wide intact walls!
                if s in intact_hm and (intact_hm[s][2] - intact_hm[s][1]) >= 0.39:
                    bp = min(r1(ba - TICK), floor_tick(res - 0.095))
                    ap = max(r1(bb + TICK), ceil_tick(res + 0.095))
                    if pos < 45 and bp > bb + EPS and bp > intact_hm[s][1] + EPS:
                        want_b = (bp, min(35, 45 - pos))
                    if pos > -45 and ap < ba - EPS and ap < intact_hm[s][2] - EPS:
                        want_a = (ap, min(35, 45 + pos))
                # Always work passive inventory exits near HouseMid!
                if pos > 0:
                    want_a = (max(r1(bb + TICK), ceil_tick(res + 0.02)), pos)
                elif pos < 0:
                    want_b = (min(r1(ba - TICK), floor_tick(res - 0.02)), -pos)
                self.sync_quotes(s, pos, mine, want_b, want_a, keep_ratio=0.40, reserve=5)
