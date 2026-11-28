"""Wave 1 Experimental Strategies for Second Brain Strategy Lab (`bot/backtest.py` compatible).

Obeys all Optibook hard constraints:
- Tick size: 0.10
- Position limit: +-100 lots per instrument (including open orders)
- Max open order volume: <= 200 lots per instrument
- Rate limit: strictly < 25 requests/sec across all instruments (enforced via sliding 1.0s window <= 22/s)
- Zero self-trades: always cancels conflicting opposite orders before inserting
"""
from __future__ import annotations

import math
import time
from collections import deque

TICK = 0.1
EPS = 1e-9
POS_LIMIT = 100


def r1(x: float) -> float:
    return round(x + 0.0, 1)


def floor_tick(x: float) -> float:
    return round(math.floor(x / TICK + 1e-9) * TICK, 1)


def ceil_tick(x: float) -> float:
    return round(math.ceil(x / TICK - 1e-9) * TICK, 1)


class SafeExecMixin:
    """Shared rate-limit, book-stripping, and zero-self-trade execution helper."""

    MAX_REQ_PER_SEC = 22

    def _init_exec(self, ex):
        self.ex = ex
        self.insts = list(getattr(ex, "instruments", [])) or sorted(ex.get_tradable_instruments().keys())
        self.req_ts = deque()

    def _now(self) -> float:
        return time.time()

    def _budget(self) -> int:
        now = self._now()
        while self.req_ts and now - self.req_ts[0] >= 1.0 - 1e-9:
            self.req_ts.popleft()
        return max(0, self.MAX_REQ_PER_SEC - len(self.req_ts))

    def _use_req(self) -> bool:
        if self._budget() <= 0:
            return False
        self.req_ts.append(self._now())
        return True

    def _clean_book(self, inst: str):
        raw = self.ex.get_last_price_book(inst)
        mine = self.ex.get_outstanding_orders(inst)
        if raw is None:
            return [], [], mine
        own_b, own_a = {}, {}
        for o in mine.values():
            d = own_b if o.side == "bid" else own_a
            p = r1(o.price)
            d[p] = d.get(p, 0) + int(o.volume)
        bids = [(r1(l.price), int(l.volume) - own_b.get(r1(l.price), 0)) for l in (raw.bids or [])]
        asks = [(r1(l.price), int(l.volume) - own_a.get(r1(l.price), 0)) for l in (raw.asks or [])]
        return [x for x in bids if x[1] > 0], [x for x in asks if x[1] > 0], mine

    def _cancel_all(self, inst: str, mine: dict) -> bool:
        if not mine:
            return True
        if not self._use_req():
            return False
        self.ex.delete_orders(inst)
        mine.clear()
        return True

    def _send_ioc(self, inst: str, side: str, price: float, vol: int, pos: int, mine: dict) -> int:
        if vol <= 0:
            return 0
        # Cancel conflicting opposite orders first to prevent self-trade
        opp = "ask" if side == "bid" else "bid"
        conflict = any(
            o.side == opp and ((side == "bid" and o.price <= price + EPS) or (side == "ask" and o.price >= price - EPS))
            for o in mine.values()
        )
        same_open = sum(o.volume for o in mine.values() if o.side == side)
        # Also if same_open blocks position headroom, cancel all first
        headroom = (POS_LIMIT - pos - same_open) if side == "bid" else (POS_LIMIT + pos - same_open)
        if conflict or (headroom < vol and same_open > 0):
            if not self._cancel_all(inst, mine):
                return 0
            same_open = 0
        max_v = (POS_LIMIT - pos - same_open) if side == "bid" else (POS_LIMIT + pos - same_open)
        v = min(vol, max_v)
        if v <= 0 or not self._use_req():
            return 0
        resp = self.ex.insert_order(inst, price=r1(price), volume=int(v), side=side, order_type="ioc")
        return int(v) if getattr(resp, "success", False) else 0

    def _sync_quotes(self, inst: str, pos: int, mine: dict, want_bid, want_ask, price_tol: float = 0.0):
        """Queue-preserving sync for passive bid/ask quotes."""
        # Enforce no self-cross in desired quotes
        if want_bid and want_ask and want_bid[0] >= want_ask[0] - EPS:
            if pos > 0:
                want_bid = None
            elif pos < 0:
                want_ask = None
            else:
                want_bid = want_ask = None

        cur_bids = [o for o in mine.values() if o.side == "bid"]
        cur_asks = [o for o in mine.values() if o.side == "ask"]

        def matches(cur_list, want):
            if want is None:
                return len(cur_list) == 0
            if len(cur_list) != 1:
                return False
            o = cur_list[0]
            return abs(o.price - want[0]) <= price_tol + EPS and abs(o.volume - want[1]) <= max(2, want[1] // 4)

        b_ok = matches(cur_bids, want_bid)
        a_ok = matches(cur_asks, want_ask)
        if b_ok and a_ok:
            return

        # Check if new order would cross existing opposite resting order -> cancel opposite first!
        if not b_ok and want_bid and any(o.price <= want_bid[0] + EPS for o in cur_asks):
            if not self._cancel_all(inst, mine):
                return
            cur_bids, cur_asks = [], []
            b_ok = a_ok = False
        if not a_ok and want_ask and any(o.price >= want_ask[0] - EPS for o in cur_bids):
            if not self._cancel_all(inst, mine):
                return
            cur_bids, cur_asks = [], []
            b_ok = a_ok = False

        # If both sides need changing and mine has >=2 orders, single delete_orders saves requests!
        if not b_ok and not a_ok and len(mine) >= 2:
            if not self._cancel_all(inst, mine):
                return
            cur_bids, cur_asks = [], []

        if not b_ok:
            for o in list(cur_bids):
                if not self._use_req():
                    return
                self.ex.delete_order(inst, order_id=o.order_id)
                mine.pop(o.order_id, None)
            if want_bid:
                open_b = sum(o.volume for o in mine.values() if o.side == "bid")
                v = min(want_bid[1], POS_LIMIT - pos - open_b)
                if v > 0 and self._use_req():
                    self.ex.insert_order(inst, price=r1(want_bid[0]), volume=int(v), side="bid", order_type="limit")

        if not a_ok:
            for o in list(cur_asks):
                if not self._use_req():
                    return
                self.ex.delete_order(inst, order_id=o.order_id)
                mine.pop(o.order_id, None)
            if want_ask:
                open_a = sum(o.volume for o in mine.values() if o.side == "ask")
                v = min(want_ask[1], POS_LIMIT + pos - open_a)
                if v > 0 and self._use_req():
                    self.ex.insert_order(inst, price=r1(want_ask[0]), volume=int(v), side="ask", order_type="limit")


class CausalFairValueTracker:
    """Causal online Fair-Value estimator exploiting:
    1) Slow-adapting linear cointegration + instant leader move on (NVDA->SNDK, AVGO->NBIS, MU->CRWV)
    2) Autoregressive 1s momentum on CRWV & NBIS
    3) Wall-midpoint anchor (V>=400) + capped Microprice on all stocks
    """

    # Prior slopes from Chunk-0 physics (intercept adapts online immediately on first tick!)
    PAIRS = {
        "SNDK": ("NVDA", 0.421, 0.002),   # (leader, beta, alpha_ema_rate per 0.2s step ~ 100s HL)
        "NBIS": ("AVGO", 0.402, 0.002),
        "CRWV": ("MU",   1.088, 0.002),
    }

    def __init__(self):
        self.alpha_ema = {}
        self.mid_hist = {s: deque(maxlen=40) for s in ["AMD", "AVGO", "CRWV", "MU", "NBIS", "NVDA", "SNDK", "VRT"]}

    def update(self, now: float, books: dict) -> dict:
        mids = {}
        micro_shift = {}
        wall_shift = {}

        for s, (bids, asks) in books.items():
            if not bids or not asks:
                continue
            bb, bv = bids[0]
            ba, av = asks[0]
            mid = 0.5 * (bb + ba)
            mids[s] = mid
            self.mid_hist.setdefault(s, deque(maxlen=40)).append((now, mid))

            bvc, avc = min(bv, 120), min(av, 120)
            micro = (bb * avc + ba * bvc) / max(1, bvc + avc)
            micro_shift[s] = micro - mid

            wb = next((p for p, v in bids if v >= 400), None)
            wa = next((p for p, v in asks if v >= 400), None)
            wall_shift[s] = (0.5 * (wb + wa) - mid) if (wb is not None and wa is not None) else 0.0

        fvs = {}
        for s, mid in mids.items():
            if s in self.PAIRS:
                lead, beta, rate = self.PAIRS[s]
                if lead in mids:
                    raw_a = mid - beta * mids[lead]
                    if s not in self.alpha_ema:
                        self.alpha_ema[s] = raw_a
                    else:
                        self.alpha_ema[s] = (1.0 - rate) * self.alpha_ema[s] + rate * raw_a
                    coint_fv = self.alpha_ema[s] + beta * mids[lead]

                    # Add 1s autoregressive continuation on CRWV and NBIS
                    ar_shift = 0.0
                    if s in ("CRWV", "NBIS"):
                        h = self.mid_hist[s]
                        # Find mid ~1.0s ago
                        m_1s = mid
                        for ts, mp in reversed(h):
                            if now - ts >= 0.8:
                                m_1s = mp
                                break
                        ar_shift = 0.28 * (mid - m_1s)

                    fvs[s] = coint_fv + ar_shift + 0.25 * micro_shift[s]
                else:
                    fvs[s] = mid + 0.5 * micro_shift[s]
            else:
                # Leader / standalone stocks: wall_mid + microprice
                w_wt = 0.70 if s in ("MU", "VRT") else 0.45
                fvs[s] = mid + w_wt * wall_shift[s] + 0.45 * micro_shift[s]
        return fvs


class CointSniper(SafeExecMixin):
    """S1 Prototype: Pure Aggressive/Passive Follower Sniper on SNDK, NBIS, CRWV."""

    TARGETS = ("SNDK", "NBIS", "CRWV")
    ENTRY_EDGE = 0.08     # Buy ask when FV - ask >= 0.08; Sell bid when bid - FV >= 0.08
    SKEW_PER_LOT = 0.0012

    def __init__(self, ex, folder=None):
        self._init_exec(ex)
        self.fv_model = CausalFairValueTracker()

    def step(self):
        books = {}
        mines = {}
        for s in self.insts:
            b, a, m = self._clean_book(s)
            books[s] = (b, a)
            mines[s] = m

        fvs = self.fv_model.update(self._now(), books)
        positions = self.ex.get_positions()

        for s in self.TARGETS:
            if s not in fvs:
                continue
            bids, asks = books[s]
            if not bids or not asks:
                continue
            mine = mines[s]
            pos = int(positions.get(s, 0))
            fv = fvs[s] - pos * self.SKEW_PER_LOT
            bb, bv = bids[0]
            ba, av = asks[0]

            # 1. Aggressive IOC entry/flip when touch is strictly mispriced vs skewed FV
            if fv - ba >= self.ENTRY_EDGE and pos < POS_LIMIT:
                # Sweep all mispriced ask levels up to fv - ENTRY_EDGE!
                sweep_p = floor_tick(fv - self.ENTRY_EDGE)
                sweep_v = sum(v for p, v in asks if p <= sweep_p + EPS)
                if sweep_v > 0:
                    sent = self._send_ioc(s, "bid", sweep_p, sweep_v, pos, mine)
                    pos += sent
            elif bb - fv >= self.ENTRY_EDGE and pos > -POS_LIMIT:
                sweep_p = ceil_tick(fv + self.ENTRY_EDGE)
                sweep_v = sum(v for p, v in bids if p >= sweep_p - EPS)
                if sweep_v > 0:
                    sent = self._send_ioc(s, "ask", sweep_p, sweep_v, pos, mine)
                    pos -= sent

            # 2. Passive exit quote at skewed FV if holding inventory
            want_b = want_a = None
            if pos > 0:
                ask_px = max(r1(bb + TICK), ceil_tick(fv + 0.02))
                want_a = (ask_px, pos)
            elif pos < 0:
                bid_px = min(r1(ba - TICK), floor_tick(fv - 0.02))
                want_b = (bid_px, -pos)
            self._sync_quotes(s, pos, mine, want_b, want_a)


class FairValueMaker(SafeExecMixin):
    """S2 Prototype: 100% Passive Limit Market Maker on Predictable Followers + Wide-Spread Queue=0."""

    def __init__(self, ex, folder=None):
        self._init_exec(ex)
        self.fv_model = CausalFairValueTracker()

    def step(self):
        books = {}
        mines = {}
        for s in self.insts:
            b, a, m = self._clean_book(s)
            books[s] = (b, a)
            mines[s] = m

        fvs = self.fv_model.update(self._now(), books)
        positions = self.ex.get_positions()

        # Prioritize follower stocks first, then wide-spread leaders
        order = ["SNDK", "NBIS", "CRWV", "MU", "VRT", "AVGO", "NVDA", "AMD"]
        for s in order:
            if s not in fvs or self._budget() < 3:
                continue
            bids, asks = books[s]
            if not bids or not asks:
                continue
            mine = mines[s]
            pos = int(positions.get(s, 0))
            bb, ba = bids[0][0], asks[0][0]
            spread = r1(ba - bb)

            is_follower = s in ("SNDK", "NBIS", "CRWV")
            skew = 0.0015 * pos if is_follower else 0.0040 * pos
            r_px = fvs[s] - skew
            half_sp = 0.06 if is_follower else 0.12

            bid_px = min(r1(ba - TICK), floor_tick(r_px - half_sp))
            ask_px = max(r1(bb + TICK), ceil_tick(r_px + half_sp))

            # On non-followers, ONLY enter passively if stepping inside a >=0.20 spread (queue=0!),
            # never join behind 500-lot BBO queues! Always quote exit when pos != 0.
            lot = 40 if is_follower else 25
            want_b = (bid_px, lot) if pos < POS_LIMIT else None
            want_a = (ask_px, lot) if pos > -POS_LIMIT else None

            if not is_follower:
                if pos >= 0 and want_b and want_b[0] <= bb + EPS:
                    want_b = None  # don't join behind queue on random-walk leaders
                if pos <= 0 and want_a and want_a[0] >= ba - EPS:
                    want_a = None
                if pos > 0:
                    want_a = (max(r1(bb + TICK), ceil_tick(r_px + 0.03)), pos)
                elif pos < 0:
                    want_b = (min(r1(ba - TICK), floor_tick(r_px - 0.03)), -pos)

            self._sync_quotes(s, pos, mine, want_b, want_a)


class HybridAlphaMakerSniper(SafeExecMixin):
    """S3 Prototype: Combined Follower IOC Sniper + Multi-Asset FV Passive Market Maker."""

    def __init__(self, ex, folder=None):
        self._init_exec(ex)
        self.fv_model = CausalFairValueTracker()

    def step(self):
        books = {}
        mines = {}
        for s in self.insts:
            b, a, m = self._clean_book(s)
            books[s] = (b, a)
            mines[s] = m

        fvs = self.fv_model.update(self._now(), books)
        positions = self.ex.get_positions()

        # Pass 1: High-EV IOC sweeps on SNDK, NBIS, CRWV
        for s in ("SNDK", "NBIS", "CRWV"):
            if s not in fvs:
                continue
            bids, asks = books[s]
            if not bids or not asks:
                continue
            mine = mines[s]
            pos = int(positions.get(s, 0))
            fv = fvs[s] - 0.0012 * pos
            bb, ba = bids[0][0], asks[0][0]
            if fv - ba >= 0.07 and pos < POS_LIMIT:
                sp_px = floor_tick(fv - 0.06)
                vol = sum(v for p, v in asks if p <= sp_px + EPS)
                sent = self._send_ioc(s, "bid", sp_px, vol, pos, mine)
                positions[s] = pos + sent
            elif bb - fv >= 0.07 and pos > -POS_LIMIT:
                sp_px = ceil_tick(fv + 0.06)
                vol = sum(v for p, v in bids if p >= sp_px - EPS)
                sent = self._send_ioc(s, "ask", sp_px, vol, pos, mine)
                positions[s] = pos - sent

        # Pass 2: Passive quoting around reservation price
        for s in ("SNDK", "NBIS", "CRWV", "MU", "VRT"):
            if s not in fvs or self._budget() < 3:
                continue
            bids, asks = books[s]
            if not bids or not asks:
                continue
            mine = mines[s]
            pos = int(positions.get(s, 0))
            bb, ba = bids[0][0], asks[0][0]
            is_fol = s in ("SNDK", "NBIS", "CRWV")
            r_px = fvs[s] - (0.0012 if is_fol else 0.0035) * pos
            half_sp = 0.05 if is_fol else 0.09
            bid_px = min(r1(ba - TICK), floor_tick(r_px - half_sp))
            ask_px = max(r1(bb + TICK), ceil_tick(r_px + half_sp))
            lot = 45 if is_fol else 25
            want_b = (bid_px, lot) if pos < POS_LIMIT else None
            want_a = (ask_px, lot) if pos > -POS_LIMIT else None
            if not is_fol:
                if pos >= 0 and want_b and want_b[0] <= bb + EPS:
                    want_b = None
                if pos <= 0 and want_a and want_a[0] >= ba - EPS:
                    want_a = None
                if pos > 0:
                    want_a = (max(r1(bb + TICK), ceil_tick(r_px + 0.02)), pos)
                elif pos < 0:
                    want_b = (min(r1(ba - TICK), floor_tick(r_px - 0.02)), -pos)
            self._sync_quotes(s, pos, mine, want_b, want_a)
