"""Shared Optibook-compliant execution & house-book utilities for high-capacity strategies.

Strictly enforces all Optibook & `bot/backtest.py` rules:
- Tick grid: 0.10
- Position limit: +-100 lots per stock (including open orders)
- Total open order volume: <= 190 lots per stock (under Optibook's 200-lot cap)
- Request rate limit: <= 22 req/s across all 8 stocks (under Optibook's 25 req/s disconnect)
- Zero self-trades: cancels any crossing opposite resting order before inserting
"""
from __future__ import annotations

import math
import time
from collections import deque
from typing import Dict, List, Optional, Tuple

TICK = 0.1
EPS = 1e-9
POS_LIMIT = 100
MAX_OPEN_VOL = 190
LEADERS = {"AMD", "AVGO", "MU", "NVDA", "VRT"}
FOLLOWERS = {"CRWV", "NBIS", "SNDK"}
ALL_STOCKS = ("AMD", "AVGO", "CRWV", "MU", "NBIS", "NVDA", "SNDK", "VRT")


def r1(x: float) -> float:
    return round(x + 0.0, 1)


def floor_tick(x: float) -> float:
    return round(math.floor(x / TICK + 1e-9) * TICK, 1)


def ceil_tick(x: float) -> float:
    return round(math.ceil(x / TICK - 1e-9) * TICK, 1)


def strict_leader_house_mid(bids: List[Tuple[float, int]], asks: List[Tuple[float, int]]) -> Optional[Tuple[float, float, float]]:
    """Return (house_mid, wall_bid, wall_ask) on leaders ONLY when both 3000-lot walls are intact (>=2400).
    Avoids false 1-tick shifts when one wall is partially consumed."""
    wb = next((p for p, v in bids if v >= 2400), None)
    wa = next((p for p, v in asks if v >= 2400), None)
    if wb is not None and wa is not None and wa > wb + 0.15:
        return 0.5 * (wb + wa), wb, wa
    # Fallback to 2000-lot second tier on both sides if symmetric
    wb2 = next((p for p, v in bids if v >= 1700), None)
    wa2 = next((p for p, v in asks if v >= 1700), None)
    if wb2 is not None and wa2 is not None and wa2 > wb2 + 0.35:
        return 0.5 * (wb2 + wa2), r1(wb2 + 0.1), r1(wa2 - 0.1)
    return None


def follower_house_mid(bids: List[Tuple[float, int]], asks: List[Tuple[float, int]]) -> Optional[Tuple[float, float, float]]:
    """Return (house_mid, wall_bid, wall_ask) on followers from LivelyLasagnas 1000-lot walls (>=850)."""
    wb = next((p for p, v in bids if v >= 850), None)
    wa = next((p for p, v in asks if v >= 850), None)
    if wb is not None and wa is not None and wa > wb + 0.25:
        return 0.5 * (wb + wa), wb, wa
    return None


class OptibookExecEngine:
    """Zero-disconnect, queue-preserving, <=22 req/s execution base class."""

    MAX_REQ_PER_SEC = 22

    def _init_engine(self, ex):
        self.ex = ex
        self.insts = list(getattr(ex, "instruments", [])) or sorted(ex.get_tradable_instruments().keys())
        self.req_ts: deque = deque()

    def now(self) -> float:
        clk = getattr(self.ex, "clock", None)
        return clk.time() if clk is not None else time.time()

    def budget(self, reserve: int = 0) -> int:
        t = self.now()
        while self.req_ts and t - self.req_ts[0] >= 1.0 - 1e-9:
            self.req_ts.popleft()
        return max(0, self.MAX_REQ_PER_SEC - reserve - len(self.req_ts))

    def _spend(self, reserve: int = 0) -> bool:
        if self.budget(reserve) <= 0:
            return False
        self.req_ts.append(self.now())
        return True

    def clean_book(self, inst: str):
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

    def cancel_all(self, inst: str, mine: dict, reserve: int = 0) -> bool:
        if not mine:
            return True
        if not self._spend(reserve):
            return False
        self.ex.delete_orders(inst)
        mine.clear()
        return True

    def pull_adverse_quotes(self, inst: str, mine: dict, unskewed_fv: float, tol: float = 0.005):
        """Cancel resting orders ONLY if Fair Value has moved through our resting price (negative EV)."""
        if not mine:
            return
        bad_b = [o for o in mine.values() if o.side == "bid" and o.price > unskewed_fv + tol]
        bad_a = [o for o in mine.values() if o.side == "ask" and o.price < unskewed_fv - tol]
        if bad_b and bad_a:
            self.cancel_all(inst, mine, reserve=0)
        else:
            for o in bad_b + bad_a:
                if self._spend(reserve=0):
                    self.ex.delete_order(inst, order_id=o.order_id)
                    mine.pop(o.order_id, None)

    def sweep_ioc(self, inst: str, side: str, limit_px: float, levels: list, pos: int, mine: dict, cap_pos: int = POS_LIMIT) -> int:
        """Sweep mispriced book liquidity up to limit_px, up to +-cap_pos."""
        if side == "bid":
            avail = sum(v for p, v in levels if p <= limit_px + EPS)
            want = cap_pos - pos
        else:
            avail = sum(v for p, v in levels if p >= limit_px - EPS)
            want = cap_pos + pos
        vol = min(avail, want)
        if vol <= 0:
            return 0

        opp = "ask" if side == "bid" else "bid"
        conflict = any(
            o.side == opp and ((side == "bid" and o.price <= limit_px + EPS) or (side == "ask" and o.price >= limit_px - EPS))
            for o in mine.values()
        )
        same_open = sum(o.volume for o in mine.values() if o.side == side)
        headroom = (POS_LIMIT - pos - same_open) if side == "bid" else (POS_LIMIT + pos - same_open)
        if conflict or (headroom < vol and same_open > 0):
            if not self.cancel_all(inst, mine, reserve=0):
                return 0
            same_open = 0

        headroom = (POS_LIMIT - pos - same_open) if side == "bid" else (POS_LIMIT + pos - same_open)
        v = min(vol, headroom)
        if v <= 0 or not self._spend(reserve=0):
            return 0
        resp = self.ex.insert_order(inst, price=r1(limit_px), volume=int(v), side=side, order_type="ioc")
        if getattr(resp, "success", False):
            # Re-read actual position from exchange if available so partial/multi-level fills are exact
            return int(self.ex.get_positions().get(inst, pos)) - pos
        return 0

    def size_two_sided(
        self,
        pos: int,
        bid_px: Optional[float],
        ask_px: Optional[float],
        unskewed_fv: float,
        entry_cap: int = POS_LIMIT,
        min_flip_edge: float = 0.04,
    ) -> Tuple[Optional[Tuple[float, int]], Optional[Tuple[float, int]]]:
        """Compute (want_bid, want_ask) obeying +-POS_LIMIT and <=190 total open order volume.
        Allows unwind+flip (>100 order when reducing inventory) ONLY if price has positive standalone edge!"""
        b_vol = 0
        if bid_px is not None:
            if bid_px <= unskewed_fv - min_flip_edge:
                b_vol = max(0, entry_cap - pos)
            elif pos < 0:
                b_vol = -pos  # pure inventory cover, do not flip long at thin edge

        a_vol = 0
        if ask_px is not None:
            if ask_px >= unskewed_fv + min_flip_edge:
                a_vol = max(0, entry_cap + pos)
            elif pos > 0:
                a_vol = pos   # pure inventory exit, do not flip short at thin edge

        # Enforce Optibook total open volume <= MAX_OPEN_VOL (190 < 200)
        if b_vol + a_vol > MAX_OPEN_VOL:
            if pos > 0:
                a_vol = min(a_vol, MAX_OPEN_VOL)
                b_vol = max(0, min(b_vol, MAX_OPEN_VOL - a_vol))
            elif pos < 0:
                b_vol = min(b_vol, MAX_OPEN_VOL)
                a_vol = max(0, min(a_vol, MAX_OPEN_VOL - b_vol))
            else:
                half = MAX_OPEN_VOL // 2
                b_vol = min(b_vol, half)
                a_vol = min(a_vol, MAX_OPEN_VOL - b_vol)

        wb = (r1(bid_px), int(b_vol)) if (bid_px is not None and b_vol > 0) else None
        wa = (r1(ask_px), int(a_vol)) if (ask_px is not None and a_vol > 0) else None
        return wb, wa

    def sync_quotes(
        self,
        inst: str,
        pos: int,
        mine: dict,
        want_bid: Optional[Tuple[float, int]],
        want_ask: Optional[Tuple[float, int]],
        keep_ratio: float = 0.35,
        reserve: int = 2,
    ):
        """Queue-preserving sync: keeps front-of-queue order if price matches & >=keep_ratio volume remains."""
        if want_bid and want_ask and want_bid[0] >= want_ask[0] - EPS:
            if pos > 0:
                want_bid = None
            elif pos < 0:
                want_ask = None
            else:
                want_bid = want_ask = None

        cur_bids = [o for o in mine.values() if o.side == "bid"]
        cur_asks = [o for o in mine.values() if o.side == "ask"]

        def ok(cur_list, want, side: str) -> bool:
            if want is None:
                return len(cur_list) == 0
            if len(cur_list) != 1:
                return False
            o = cur_list[0]
            if abs(o.price - want[0]) > EPS:
                return False
            if side == "bid" and want_ask and o.price >= want_ask[0] - EPS:
                return False
            if side == "ask" and want_bid and o.price <= want_bid[0] + EPS:
                return False
            min_v = max(8, int(want[1] * keep_ratio))
            return min_v <= o.volume <= want[1] + 15

        b_ok = ok(cur_bids, want_bid, "bid")
        a_ok = ok(cur_asks, want_ask, "ask")
        if b_ok and a_ok:
            return

        # Prevent self-cross
        if (not b_ok and want_bid and any(o.price <= want_bid[0] + EPS for o in cur_asks)) or \
           (not a_ok and want_ask and any(o.price >= want_ask[0] - EPS for o in cur_bids)) or \
           (not b_ok and not a_ok and len(mine) >= 2):
            if not self.cancel_all(inst, mine, reserve=reserve):
                return
            cur_bids, cur_asks = [], []
            b_ok = a_ok = False

        # Also check total open volume cap before inserting
        if not b_ok:
            if want_bid and len(cur_bids) == 1 and abs(cur_bids[0].price - want_bid[0]) < EPS and cur_bids[0].volume > want_bid[1]:
                if self._spend(reserve=reserve):
                    self.ex.amend_order(inst, order_id=cur_bids[0].order_id, volume=int(want_bid[1]))
                    cur_bids[0].volume = int(want_bid[1])
            else:
                for o in list(cur_bids):
                    if not self._spend(reserve=reserve):
                        return
                    self.ex.delete_order(inst, order_id=o.order_id)
                    mine.pop(o.order_id, None)
                if want_bid:
                    open_b = sum(o.volume for o in mine.values() if o.side == "bid")
                    open_tot = sum(o.volume for o in mine.values())
                    v = min(want_bid[1], POS_LIMIT - pos - open_b, MAX_OPEN_VOL - open_tot)
                    if v > 0 and self._spend(reserve=reserve):
                        resp = self.ex.insert_order(inst, price=want_bid[0], volume=int(v), side="bid", order_type="limit")
                        if getattr(resp, "order_id", None) is not None:
                            mine[resp.order_id] = type("O", (), {"order_id": resp.order_id, "side": "bid", "price": want_bid[0], "volume": int(v)})()

        if not a_ok:
            if want_ask and len(cur_asks) == 1 and abs(cur_asks[0].price - want_ask[0]) < EPS and cur_asks[0].volume > want_ask[1]:
                if self._spend(reserve=reserve):
                    self.ex.amend_order(inst, order_id=cur_asks[0].order_id, volume=int(want_ask[1]))
                    cur_asks[0].volume = int(want_ask[1])
            else:
                for o in list(cur_asks):
                    if not self._spend(reserve=reserve):
                        return
                    self.ex.delete_order(inst, order_id=o.order_id)
                    mine.pop(o.order_id, None)
                if want_ask:
                    open_a = sum(o.volume for o in mine.values() if o.side == "ask")
                    open_tot = sum(o.volume for o in mine.values())
                    v = min(want_ask[1], POS_LIMIT + pos - open_a, MAX_OPEN_VOL - open_tot)
                    if v > 0 and self._spend(reserve=reserve):
                        resp = self.ex.insert_order(inst, price=want_ask[0], volume=int(v), side="ask", order_type="limit")
                        if getattr(resp, "order_id", None) is not None:
                            mine[resp.order_id] = type("O", (), {"order_id": resp.order_id, "side": "ask", "price": want_ask[0], "volume": int(v)})()
