"""Strategy W5: Student-Book Microstructure, OFI & Queue-Jumping Engine (`OrderBookMicrostructureBot`).

Quantitative Architecture:
- Strips LivelyLasagnas's house walls (>=850 on followers, >=2200 on leaders) from every PriceBook
  to isolate the PURE STUDENT ORDER BOOK!
- Fuses 4 microstructure signals on top of structural cross-asset fair value:
    1) Multi-level Student Order Book Imbalance (`StudentOBI` across all levels inside house walls)
    2) Volume-capped Stoikov Microprice
    3) 2-second BBO Order Flow Imbalance (`OFI_2s`)
    4) Volatility-adaptive half-spread (`delta_t = base_half_spread + vol_mult * sigma_1s`)
- Executes queue-jumping passive quotes (`best_bid + 0.1` / `best_ask - 0.1` when spread >= 0.20,
  or joining thin student queues `<= 40` lots when edge >= 0.04) + composite-signal IOC sweeps.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Dict, Optional, Tuple

from optibook_core import (
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


class OrderBookMicrostructureBot(OptibookExecEngine):
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
        self.prev_bbo: Dict[str, Tuple[float, int, float, int]] = {}
        self.ofi_ema: Dict[str, float] = {s: 0.0 for s in self.insts}
        self.vol_ema: Dict[str, float] = {s: 0.05 for s in self.insts}
        self.prev_mid: Dict[str, float] = {}
        self.mid_hist: Dict[str, deque] = {s: deque(maxlen=25) for s in self.insts}
        self.last_t: Optional[float] = None

    @staticmethod
    def _student_obi(inst: str, bids: list, asks: list) -> float:
        wall_cut = 800 if inst in FOLLOWERS else 2200
        sb = sum(v for _, v in bids if v < wall_cut)
        sa = sum(v for _, v in asks if v < wall_cut)
        return (sb - sa) / (sb + sa) if (sb + sa) > 0 else 0.0

    def step(self):
        now = self.now()
        dt = 0.20 if self.last_t is None else max(0.05, min(1.0, now - self.last_t))
        self.last_t = now

        books, mines, bbo_mids, lead_ref, micro_edge = {}, {}, {}, {}, {}
        for s in self.insts:
            b, a, m = self.clean_book(s)
            books[s] = (b, a)
            mines[s] = m
            if not b or not a:
                continue
            bb, raw_bv = b[0]
            ba, raw_av = a[0]
            bv, av = min(raw_bv, 100), min(raw_av, 100)
            mid = 0.5 * (bb + ba)
            bbo_mids[s] = mid
            self.mid_hist.setdefault(s, deque(maxlen=25)).append((now, mid))

            # Update short-term realized volatility & OFI
            if s in self.prev_mid:
                dm = abs(mid - self.prev_mid[s])
                self.vol_ema[s] = 0.96 * self.vol_ema.get(s, 0.05) + 0.04 * dm
            self.prev_mid[s] = mid

            if s in self.prev_bbo:
                pbb, pbv, pba, pav = self.prev_bbo[s]
                db = (bv if bb >= pbb else 0) - (pbv if bb <= pbb else 0)
                da = (av if ba <= pba else 0) - (pav if ba >= pba else 0)
                self.ofi_ema[s] = 0.80 * self.ofi_ema.get(s, 0.0) + 0.20 * float(db - da)
            self.prev_bbo[s] = (bb, bv, ba, av)

            micro = (bb * av + ba * bv) / max(1, bv + av) - mid
            st_obi = self._student_obi(s, b, a)
            ofi_imp = max(-0.04, min(0.04, 0.0008 * self.ofi_ema[s]))
            micro_edge[s] = 0.30 * micro + 0.035 * st_obi + ofi_imp

            if s in LEADERS:
                info = strict_leader_house_mid(b, a)
                hm = info[0] if info is not None else mid
                lead_ref[s] = hm
                for hl in (0.50, 1.00):
                    al = 1.0 - math.exp(-dt * math.log(2.0) / hl)
                    self.lead_ema[s][hl] = (1.0 - al) * self.lead_ema[s].get(hl, hm) + al * hm
                self.lead_hist[s].append((now, hm, self.lead_ema[s][0.50], self.lead_ema[s][1.00]))

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
            raw_a = bbo_mids[s] - beta * lag_ema
            n = self.obs.get(s, 0) + 1
            self.obs[s] = n
            if n == 1:
                self.bar_alpha[s] = raw_a
            elif n <= 30:
                self.bar_alpha[s] = ((n - 1) * self.bar_alpha[s] + raw_a) / n
            else:
                self.bar_alpha[s] = 0.9990 * self.bar_alpha[s] + 0.0010 * raw_a

            ar_imp = 0.0
            if s in ("CRWV", "NBIS"):
                h = self.mid_hist[s]
                m_1s = bbo_mids[s]
                for ts, mp in reversed(h):
                    if now - ts >= 0.8:
                        m_1s = mp
                        break
                ar_imp = 0.25 * (bbo_mids[s] - m_1s)

            fvs[s] = self.bar_alpha[s] + beta * lead_ref[lead] + ar_imp + micro_edge[s]

        positions = self.ex.get_positions()

        for s in ("NBIS", "CRWV", "SNDK"):
            if s not in fvs:
                continue
            bids, asks = books[s]
            mine = mines[s]
            pos = int(positions.get(s, 0))
            fv = fvs[s]
            self.pull_adverse_quotes(s, mine, fv, tol=0.005)

            res = fv - 0.00075 * pos
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
            res = fv - 0.00075 * pos
            bb, ba = bids[0][0], asks[0][0]

            # Volatility-adaptive quoting half-spread
            half_sp = min(0.075, max(0.035, 0.035 + 0.25 * self.vol_ema.get(s, 0.04)))
            bid_px = min(r1(ba - TICK), floor_tick(res - half_sp))
            ask_px = max(r1(bb + TICK), ceil_tick(res + half_sp))
            wb, wa = self.size_two_sided(pos, bid_px, ask_px, fv, entry_cap=POS_LIMIT, min_flip_edge=0.04)
            self.sync_quotes(s, pos, mine, wb, wa, keep_ratio=0.35, reserve=2)
