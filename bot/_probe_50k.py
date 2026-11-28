from __future__ import annotations
import math, time
from collections import deque
from optibook_core import (
    ALL_STOCKS, EPS, FOLLOWERS, LEADERS, MAX_OPEN_VOL, OptibookExecEngine,
    POS_LIMIT, TICK, ceil_tick, floor_tick, follower_house_mid, r1, strict_leader_house_mid
)

class ProbeBase(OptibookExecEngine):
    HURDLE_FOL = 0.025
    HALF_SP_FOL = 0.025
    SKEW_FOL = 0.0004
    AR_WT = 0.30
    MICRO_WT = 0.30
    USE_LEADERS = True
    HURDLE_LEAD = 0.055
    HALF_SP_LEAD = 0.065
    CAP_LEAD = 100
    MAX_REQ_PER_SEC = 24
    ALPHA_RATE = 0.0015
    USE_HOUSE_OBS_FOL = False
    SNDK_LAG_MODE = True

    SPECS = {
        "SNDK": ("NVDA", 0.4250, 2.40, 0.50),
        "NBIS": ("AVGO", 0.4069, 0.40, 1.00),
        "CRWV": ("MU",   1.1214, 0.00, 1.00),
    }

    def __init__(self, ex, folder=None):
        self._init_engine(ex)
        self.lead_hist = {s: deque(maxlen=60) for s in LEADERS}
        self.lead_ema = {s: {} for s in LEADERS}
        self.bar_alpha = {}
        self.obs = {}
        self.mid_hist = {s: deque(maxlen=30) for s in ALL_STOCKS}
        self.flow_ema = {s: 0.0 for s in ALL_STOCKS}
        self.last_t = None

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
                self.mid_hist[s].append((now, mid))
                bvc, avc = min(bv, 100), min(av, 100)
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
                    if ag == "bid": sv += float(tr.volume)
                    elif ag == "ask": sv -= float(tr.volume)
            self.flow_ema[s] = math.exp(-dt / 1.2) * self.flow_ema.get(s, 0.0) + sv

        fvs = {}
        for s, (lead, beta, lag_s, hl_s) in self.SPECS.items():
            if s not in bbo_mids or lead not in lead_ref:
                continue
            hist = self.lead_hist[lead]
            if not hist:
                continue
            col = 2 if hl_s == 0.50 else 3
            lag_ema = hist[0][col]
            if self.SNDK_LAG_MODE:
                for rec in reversed(hist):
                    if rec[0] <= now - lag_s + 1e-6:
                        lag_ema = rec[col]
                        break
                ref_for_alpha = lag_ema
            else:
                ref_for_alpha = lead_ref[lead]

            obs_y = bbo_mids[s]
            if self.USE_HOUSE_OBS_FOL:
                fhm = follower_house_mid(*books[s])
                if fhm is not None:
                    obs_y = fhm[0]

            raw_a = obs_y - beta * ref_for_alpha
            n = self.obs.get(s, 0) + 1
            self.obs[s] = n
            if n == 1:
                self.bar_alpha[s] = raw_a
            elif n <= 20:
                self.bar_alpha[s] = ((n - 1) * self.bar_alpha[s] + raw_a) / n
            else:
                r = self.ALPHA_RATE
                self.bar_alpha[s] = (1.0 - r) * self.bar_alpha[s] + r * raw_a

            fv = self.bar_alpha[s] + beta * lead_ref[lead]
            if s in ("CRWV", "NBIS"):
                h = self.mid_hist[s]
                m_1s = bbo_mids[s]
                for ts, mp in reversed(h):
                    if now - ts >= 0.8:
                        m_1s = mp
                        break
                fv += self.AR_WT * (bbo_mids[s] - m_1s)

            flow_imp = max(-0.06, min(0.06, 0.0005 * self.flow_ema.get(s, 0.0)))
            fvs[s] = fv + self.MICRO_WT * micro[s] + 0.03 * st_obi[s] + flow_imp

        if self.USE_LEADERS:
            for s in LEADERS:
                if s in intact_hm:
                    hm, wb, wa = intact_hm[s]
                    fvs[s] = hm + 0.25 * micro[s] + 0.025 * st_obi[s]

        positions = self.ex.get_positions()
        order = ("NBIS", "CRWV", "SNDK", "AVGO", "AMD", "NVDA", "MU", "VRT") if self.USE_LEADERS else ("NBIS", "CRWV", "SNDK")

        # Pass 1: IOC sweeps
        for s in order:
            if s not in fvs:
                continue
            bids, asks = books[s]
            mine = mines[s]
            pos = int(positions.get(s, 0))
            fv = fvs[s]
            is_fol = s in FOLLOWERS
            self.pull_adverse_quotes(s, mine, fv, tol=0.002 if is_fol else 0.015)

            res = fv - (self.SKEW_FOL * pos if is_fol else 0.0012 * pos)
            bb, ba = bids[0][0], asks[0][0]
            hurdle = self.HURDLE_FOL if is_fol else self.HURDLE_LEAD
            cap = POS_LIMIT if is_fol else self.CAP_LEAD

            if res - ba >= hurdle and pos < cap:
                lim = floor_tick(res - max(0.01, hurdle - 0.005))
                if not is_fol:
                    lim = min(lim, r1(intact_hm[s][2] - TICK))
                if lim >= ba - EPS:
                    pos += self.sweep_ioc(s, "bid", lim, asks, pos, mine, cap_pos=cap)
            elif bb - res >= hurdle and pos > -cap:
                lim = ceil_tick(res + max(0.01, hurdle - 0.005))
                if not is_fol:
                    lim = max(lim, r1(intact_hm[s][1] + TICK))
                if lim <= bb + EPS:
                    pos += self.sweep_ioc(s, "ask", lim, bids, pos, mine, cap_pos=cap)
            positions[s] = pos

        # Pass 2: Passive quoting
        for s in order:
            if self.budget(reserve=1 if s in FOLLOWERS else 4) <= 0:
                continue
            bids, asks = books.get(s, ([], []))
            if not bids or not asks:
                continue
            mine = mines[s]
            pos = int(positions.get(s, 0))
            bb, ba = bids[0][0], asks[0][0]
            is_fol = s in FOLLOWERS

            if is_fol:
                if s not in fvs: continue
                fv = fvs[s]
                res = fv - self.SKEW_FOL * pos
                bid_px = min(r1(ba - TICK), floor_tick(res - self.HALF_SP_FOL))
                ask_px = max(r1(bb + TICK), ceil_tick(res + self.HALF_SP_FOL))
                wb, wa = self.size_two_sided(pos, bid_px, ask_px, fv, entry_cap=POS_LIMIT, min_flip_edge=0.02)
                self.sync_quotes(s, pos, mine, wb, wa, keep_ratio=0.40, reserve=1)
            else:
                fv = fvs.get(s, 0.5 * (bb + ba))
                res = fv - 0.0012 * pos
                want_b = want_a = None
                if s in intact_hm:
                    bp = min(r1(ba - TICK), floor_tick(res - self.HALF_SP_LEAD))
                    ap = max(r1(bb + TICK), ceil_tick(res + self.HALF_SP_LEAD))
                    # Step strictly inside BBO (queue=0) AND strictly inside house walls!
                    if pos < self.CAP_LEAD and bp > bb + EPS and bp > intact_hm[s][1] + EPS:
                        want_b = (bp, min(60, self.CAP_LEAD - pos))
                    if pos > -self.CAP_LEAD and ap < ba - EPS and ap < intact_hm[s][2] - EPS:
                        want_a = (ap, min(60, self.CAP_LEAD + pos))
                if pos > 0:
                    want_a = (max(r1(bb + TICK), ceil_tick(res + 0.015)), pos)
                elif pos < 0:
                    want_b = (min(r1(ba - TICK), floor_tick(res - 0.015)), -pos)
                self.sync_quotes(s, pos, mine, want_b, want_a, keep_ratio=0.40, reserve=4)

class P1_TightHurdle(ProbeBase):
    HURDLE_FOL = 0.020
    HALF_SP_FOL = 0.020
    SKEW_FOL = 0.0003
    USE_LEADERS = True
    HURDLE_LEAD = 0.055
    HALF_SP_LEAD = 0.060

class P2_UltraTight(ProbeBase):
    HURDLE_FOL = 0.010
    HALF_SP_FOL = 0.015
    SKEW_FOL = 0.00025
    USE_LEADERS = True
    HURDLE_LEAD = 0.045
    HALF_SP_LEAD = 0.050

class P3_HouseObsFol(ProbeBase):
    HURDLE_FOL = 0.020
    HALF_SP_FOL = 0.020
    SKEW_FOL = 0.0003
    USE_HOUSE_OBS_FOL = True
    ALPHA_RATE = 0.0005

class P4_RawLeadAlpha(ProbeBase):
    HURDLE_FOL = 0.020
    HALF_SP_FOL = 0.020
    SKEW_FOL = 0.0003
    SNDK_LAG_MODE = False
    ALPHA_RATE = 0.0015
