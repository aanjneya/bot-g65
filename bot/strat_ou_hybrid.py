"""Strategy W1: Two-Timescale Ornstein-Uhlenbeck Cointegration Maker-Sniper (`OUCointMakerSniper`).

Quantitative Architecture:
- Exploits the two-timescale cointegration physics of Optibook's follower triplets
  (NVDA -> SNDK, AVGO -> NBIS, MU -> CRWV):
    1) Slow equilibrium intercept bar_alpha_i (rapid 25-tick initialization, then ultra-slow
       HL ~ 140s EWMA so 12s-40s Ornstein-Uhlenbeck excursions are faded rather than chased).
    2) Noise-free strict 3000-lot symmetric LivelyLasagnas HouseMid on leaders (with smooth BBO fallback).
    3) 1-second autoregressive continuation on CRWV & NBIS + volume-capped Stoikov Microprice.
    4) Full +-100 lot capacity (up to 190 total open vol for unwind+flip) with queue-preserving
       passive limit quotes and high-conviction IOC mispricing sweeps.
"""
from __future__ import annotations

from collections import deque
from typing import Dict

from optibook_core import (
    EPS,
    FOLLOWERS,
     OptibookExecEngine,
    POS_LIMIT,
    TICK,
    ceil_tick,
    floor_tick,
    r1,
    strict_leader_house_mid,
)


class OUCointMakerSniper(OptibookExecEngine):
    PAIRS = {
        "SNDK": ("NVDA", 0.4225, 0.0010),
        "NBIS": ("AVGO", 0.4040, 0.0010),
        "CRWV": ("MU",   1.1050, 0.0010),
    }

    def __init__(self, ex, folder=None):
        self._init_engine(ex)
        self.alpha: Dict[str, float] = {}
        self.obs: Dict[str, int] = {}
        self.mid_hist: Dict[str, deque] = {s: deque(maxlen=30) for s in self.insts}

    def step(self):
        now = self.now()
        books, mines, bbo_mids, lead_px, micro = {}, {}, {}, {}, {}

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
            self.mid_hist.setdefault(s, deque(maxlen=30)).append((now, mid))

            bvc, avc = min(bv, 120), min(av, 120)
            micro[s] = (bb * avc + ba * bvc) / max(1, bvc + avc) - mid

            if s not in FOLLOWERS:
                hm_info = strict_leader_house_mid(b, a)
                lead_px[s] = hm_info[0] if hm_info is not None else mid

        fvs: Dict[str, float] = {}
        for s, (lead, beta, slow_rate) in self.PAIRS.items():
            if s not in bbo_mids or lead not in lead_px:
                continue
            mid_s = bbo_mids[s]
            lp = lead_px[lead]
            raw_a = mid_s - beta * lp
            n = self.obs.get(s, 0) + 1
            self.obs[s] = n
            if n == 1:
                self.alpha[s] = raw_a
            elif n <= 25:
                self.alpha[s] = ((n - 1) * self.alpha[s] + raw_a) / n
            else:
                self.alpha[s] = (1.0 - slow_rate) * self.alpha[s] + slow_rate * raw_a

            coint_fv = self.alpha[s] + beta * lp

            ar_shift = 0.0
            if s in ("CRWV", "NBIS"):
                h = self.mid_hist[s]
                m_1s = mid_s
                for ts, mp in reversed(h):
                    if now - ts >= 0.8:
                        m_1s = mp
                        break
                ar_shift = 0.25 * (mid_s - m_1s)

            fvs[s] = coint_fv + ar_shift + 0.25 * micro[s]

        positions = self.ex.get_positions()

        # Pass 1: Pull adverse resting quotes + execute high-EV IOC sweeps up to +-100 lots
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
                lim = floor_tick(res - 0.05)
                pos += self.sweep_ioc(s, "bid", lim, asks, pos, mine, cap_pos=POS_LIMIT)
            elif bb - res >= 0.055 and pos > -POS_LIMIT:
                lim = ceil_tick(res + 0.05)
                pos += self.sweep_ioc(s, "ask", lim, bids, pos, mine, cap_pos=POS_LIMIT)
            positions[s] = pos

        # Pass 2: Full-capacity queue-preserving passive quoting
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
