"""Shared Optibook-compliant smart execution primitives (`SmartOptibookEngine`).

Guarantees 100% compliance with all live Optibook & `bot/backtest.py` exchange rules:
- Simulation/live clock safety: ALWAYS reads `ex.clock.time()` when present (zero SimClock starvation).
- Own-order book stripping: strips our resting orders from `get_last_price_book` before any signal calc.
- Rate limit safety: strict sliding-window <= 24 req/s cap across all 8 instruments (< 25 disconnect).
- Position & open-volume caps: +-100 position limit (incl. open orders) and <= 200 open volume per stock.
- Zero self-trade guarantee: checks & cancels any crossing opposite resting order before inserting.
- Queue-preserving execution:
    1) `amend_order` volume downsizing (preserves queue priority when reducing size at the same price).
    2) Zero-cost `queue = 0` stub reload when alone at best price with spare request budget.
    3) Anti-Overpay Crossing Cap: when a limit order crosses opposite liquidity, caps resting price at
       the deepest crossed level (or `bb + TICK` / `ba - TICK` if passive) so remainder lots NEVER hang
       in empty air 2-5 ticks past BBO donating spread!
"""
from __future__ import annotations

import math
import time
from collections import deque
from typing import Dict, List, Optional, Tuple

TICK = 0.1
EPS = 1e-9
POS_LIMIT = 100
MAX_OPEN_VOL = 200


def r1(x: float) -> float:
    return round(x + 0.0, 1)


def floor_tick(x: float) -> float:
    return round(math.floor(x / TICK + 1e-9) * TICK, 1)


def ceil_tick(x: float) -> float:
    return round(math.ceil(x / TICK - 1e-9) * TICK, 1)


def detect_intact_leader_walls(
    bids: List[Tuple[float, int]], asks: List[Tuple[float, int]]
) -> Optional[Tuple[float, float, float]]:
    """Return (house_mid, wall_bid, wall_ask) when symmetric LivelyLasagnas 3000-lot walls are intact."""
    wb = next((p for p, v in bids if v >= 2400), None)
    wa = next((p for p, v in asks if v >= 2400), None)
    if wb is not None and wa is not None and wa > wb + 0.15:
        return 0.5 * (wb + wa), wb, wa
    wb2 = next((p for p, v in bids if v >= 1700), None)
    wa2 = next((p for p, v in asks if v >= 1700), None)
    if wb2 is not None and wa2 is not None and wa2 > wb2 + 0.35:
        return 0.5 * (wb2 + wa2), r1(wb2 + 0.1), r1(wa2 - 0.1)
    return None


def detect_follower_walls(
    bids: List[Tuple[float, int]], asks: List[Tuple[float, int]]
) -> Optional[Tuple[float, float, float]]:
    """Return (house_mid, wall_bid, wall_ask) from LivelyLasagnas 1000-lot follower walls."""
    wb = next((p for p, v in bids if v >= 850), None)
    wa = next((p for p, v in asks if v >= 850), None)
    if wb is not None and wa is not None and wa > wb + 0.25:
        return 0.5 * (wb + wa), wb, wa
    return None


def anti_overpay_bid_px(
    raw_bp: float, bids: List[Tuple[float, int]], asks: List[Tuple[float, int]], max_px: Optional[float] = None
) -> Tuple[float, int]:
    """Price a smart bid:
    - If raw_bp crosses asks: cap price at the highest actually-present ask <= raw_bp (sweeps 100% of
      available mispriced asks while resting any remainder at the cleared level, never higher in empty air!).
    - If passive: penny-jump at most 1 tick above external best bid (`min(raw_bp, bb + TICK)`).
    Returns `(optimal_bid_px, available_crossing_ask_vol)`."""
    if max_px is not None:
        raw_bp = min(raw_bp, max_px)
    if not asks or not bids:
        return raw_bp, 0
    bb = bids[0][0]
    ba = asks[0][0]
    if raw_bp >= ba - EPS:
        crossed = [(p, v) for p, v in asks if p <= raw_bp + EPS]
        if crossed:
            top_ask_px = crossed[-1][0]
            # Ensure remainder is at least bb + TICK if spread was wide, capped at raw_bp
            opt_px = min(raw_bp, max(top_ask_px, r1(bb + TICK)))
            cross_vol = sum(v for _, v in crossed)
            return r1(opt_px), cross_vol
    return r1(min(raw_bp, r1(bb + TICK))), 0


def anti_overpay_ask_px(
    raw_ap: float, bids: List[Tuple[float, int]], asks: List[Tuple[float, int]], min_px: Optional[float] = None
) -> Tuple[float, int]:
    """Symmetric smart ask pricing: sweeps 100% of mispriced bids >= raw_ap without underpricing
    resting ask remainders into empty air below the lowest swept bid."""
    if min_px is not None:
        raw_ap = max(raw_ap, min_px)
    if not asks or not bids:
        return raw_ap, 0
    bb = bids[0][0]
    ba = asks[0][0]
    if raw_ap <= bb + EPS:
        crossed = [(p, v) for p, v in bids if p >= raw_ap - EPS]
        if crossed:
            bot_bid_px = crossed[-1][0]
            opt_px = max(raw_ap, min(bot_bid_px, r1(ba - TICK)))
            cross_vol = sum(v for _, v in crossed)
            return r1(opt_px), cross_vol
    return r1(max(raw_ap, r1(ba - TICK))), 0


class SmartOptibookEngine:
    MAX_REQ = 24
    USE_AMEND = True

    def _init_core(self, ex):
        self.ex = ex
        inst_map = ex.get_tradable_instruments() if hasattr(ex, "get_tradable_instruments") else {}
        self.insts = list(getattr(ex, "instruments", [])) or sorted(inst_map.keys())
        self.req_ts: deque = deque()
        self._last_ref_px: Dict[str, float] = {}
        self._pcl_abs: Dict[str, float] = {}
        for s, meta in (inst_map or {}).items():
            pcl = getattr(meta, "price_change_limit", None)
            if pcl is not None:
                ac = getattr(pcl, "absolute_change", None)
                if ac is not None and float(ac) > 0:
                    self._pcl_abs[s] = float(ac)

    def _safe_price(self, inst: str, px: float, bids: List[Tuple[float, int]], asks: List[Tuple[float, int]], fv: float) -> float:
        """Clamp order price safely inside Optibook's price_change_limit to prevent forced disconnects."""
        ref = self._last_ref_px.get(inst)
        if bids and asks and (asks[0][0] - bids[0][0]) <= 3.0:
            ref = 0.5 * (bids[0][0] + asks[0][0])
            self._last_ref_px[inst] = ref
        elif ref is None:
            ref = fv if fv > 0 else (bids[0][0] if bids else (asks[0][0] if asks else px))
            self._last_ref_px[inst] = ref
        max_dev = min(3.5, 0.70 * self._pcl_abs.get(inst, 8.0), max(1.5, 0.06 * abs(ref)))
        lo = max(TICK, r1(ref - max_dev))
        hi = r1(ref + max_dev)
        return r1(max(lo, min(hi, px)))

    def now(self) -> float:
        clk = getattr(self.ex, "clock", None)
        return clk.time() if clk is not None else time.time()

    def budget(self, reserve: int = 0) -> int:
        t = self.now()
        while self.req_ts and t - self.req_ts[0] >= 1.0 - 1e-9:
            self.req_ts.popleft()
        return max(0, self.MAX_REQ - reserve - len(self.req_ts))

    def spend(self, reserve: int = 0) -> bool:
        if self.budget(reserve) <= 0:
            return False
        self.req_ts.append(self.now())
        return True

    def clean_book(self, inst: str):
        raw = self.ex.get_last_price_book(inst)
        mine = self.ex.get_outstanding_orders(inst)
        if raw is None:
            return [], [], mine
        own_b: Dict[float, int] = {}
        own_a: Dict[float, int] = {}
        for o in mine.values():
            d = own_b if o.side == "bid" else own_a
            p = r1(o.price)
            d[p] = d.get(p, 0) + int(o.volume)
        bids = [(r1(l.price), int(l.volume) - own_b.get(r1(l.price), 0)) for l in (raw.bids or [])]
        asks = [(r1(l.price), int(l.volume) - own_a.get(r1(l.price), 0)) for l in (raw.asks or [])]
        return [x for x in bids if x[1] > 0], [x for x in asks if x[1] > 0], mine

    def execute_quote_pair(
        self,
        inst: str,
        unskewed_fv: float,
        bids: List[Tuple[float, int]],
        asks: List[Tuple[float, int]],
        want_b: Optional[Tuple[float, int]],
        want_a: Optional[Tuple[float, int]],
        ot_b: str = "limit",
        ot_a: str = "limit",
        reserve: int = 0,
        reload_stub_below: int = 14,
        keep_touch_hyst: bool = False,
    ):
        mine = self.ex.get_outstanding_orders(inst)
        pos = int(self.ex.get_positions().get(inst, 0))

        # Clamp prices safely inside Optibook's last-traded price_change_limit (prevents disconnects on empty/wide books!)
        if want_b is not None:
            want_b = (self._safe_price(inst, want_b[0], bids, asks, unskewed_fv), want_b[1])
        if want_a is not None:
            want_a = (self._safe_price(inst, want_a[0], bids, asks, unskewed_fv), want_a[1])

        # Queue-preserving tick-boundary hysteresis on followers: never burn 2 requests canceling a front-of-queue
        # passive order at BBO when want_px flickered by 1 tick and resting order still has >= 3.2c standalone edge!
        if keep_touch_hyst and bids and asks:
            bb, ba = bids[0][0], asks[0][0]
            cb0 = [o for o in mine.values() if o.side == "bid"]
            ca0 = [o for o in mine.values() if o.side == "ask"]
            if want_b and ot_b == "limit" and want_b[0] < ba - EPS and len(cb0) == 1:
                if (
                    abs(cb0[0].price - want_b[0]) <= TICK + EPS
                    and cb0[0].price >= bb - EPS
                    and cb0[0].price <= unskewed_fv - 0.032
                    and cb0[0].volume >= 15
                ):
                    want_b = (cb0[0].price, want_b[1])
            if want_a and ot_a == "limit" and want_a[0] > bb + EPS and len(ca0) == 1:
                if (
                    abs(ca0[0].price - want_a[0]) <= TICK + EPS
                    and ca0[0].price <= ba + EPS
                    and ca0[0].price >= unskewed_fv + 0.032
                    and ca0[0].volume >= 15
                ):
                    want_a = (ca0[0].price, want_a[1])

        if want_b and want_a and want_b[0] >= want_a[0] - EPS:
            if pos >= 0:
                want_b = (r1(want_a[0] - TICK), want_b[1])
            else:
                want_a = (r1(want_b[0] + TICK), want_a[1])

        cur_b = [o for o in mine.values() if o.side == "bid"]
        cur_a = [o for o in mine.values() if o.side == "ask"]

        def side_ok(cur_list, want, ot: str, side: str) -> bool:
            if want is None:
                return len(cur_list) == 0
            if ot != "limit" or len(cur_list) != 1:
                return False
            o = cur_list[0]
            if side == "bid" and want_a and o.price >= want_a[0] - EPS:
                return False
            if side == "ask" and want_b and o.price <= want_b[0] + EPS:
                return False
            if abs(o.price - want[0]) < EPS:
                if o.volume > want[1] + 45:
                    return False
                if reload_stub_below > 0 and o.volume < min(reload_stub_below, max(1, want[1] // 2)):
                    if self.budget(reserve + 6) > 0:
                        lvls = bids if side == "bid" else asks
                        if sum(v for p, v in lvls if abs(p - o.price) < EPS) == 0:
                            return False
                return o.volume >= 1
            if self.budget(reserve + 5) <= 0 and abs(o.price - want[0]) <= TICK + EPS:
                if side == "bid" and o.price <= unskewed_fv - 0.025 and o.volume >= 4:
                    return True
                if side == "ask" and o.price >= unskewed_fv + 0.025 and o.volume >= 4:
                    return True
            return False

        b_ok = side_ok(cur_b, want_b, ot_b, "bid")
        a_ok = side_ok(cur_a, want_a, ot_a, "ask")
        if b_ok and a_ok:
            return

        cross_conflict = (
            (not b_ok and want_b and any(o.price <= want_b[0] + EPS for o in cur_a))
            or (not a_ok and want_a and any(o.price >= want_a[0] - EPS for o in cur_b))
        )
        if cross_conflict or (not b_ok and not a_ok and len(mine) >= 2):
            if not self.spend(0 if cross_conflict else reserve):
                return
            self.ex.delete_orders(inst)
            cur_b, cur_a = [], []
            b_ok = a_ok = False

        if not b_ok:
            mine = self.ex.get_outstanding_orders(inst)
            cur_b = [x for x in mine.values() if x.side == "bid"]
            # Queue-preserving amend_order when downsizing at exact same price!
            if (
                self.USE_AMEND
                and want_b
                and ot_b == "limit"
                and len(cur_b) == 1
                and abs(cur_b[0].price - want_b[0]) < EPS
                and 0 < want_b[1] < cur_b[0].volume
                and (want_a is None or cur_b[0].price < want_a[0] - EPS)
            ):
                if self.spend(reserve):
                    self.ex.amend_order(inst, order_id=cur_b[0].order_id, volume=int(want_b[1]))
            else:
                for o in cur_b:
                    if not self.spend(reserve):
                        return
                    self.ex.delete_order(inst, order_id=o.order_id)
                if want_b:
                    pos = int(self.ex.get_positions().get(inst, 0))
                    mine = self.ex.get_outstanding_orders(inst)
                    if not any(o.side == "ask" and o.price <= want_b[0] + EPS for o in mine.values()):
                        open_b = sum(o.volume for o in mine.values() if o.side == "bid")
                        open_tot = sum(o.volume for o in mine.values())
                        v = min(want_b[1], POS_LIMIT - pos - open_b, MAX_OPEN_VOL - open_tot)
                        if v > 0 and self.spend(0 if ot_b == "ioc" else reserve):
                            self.ex.insert_order(inst, price=want_b[0], volume=int(v), side="bid", order_type=ot_b)

        if not a_ok:
            mine = self.ex.get_outstanding_orders(inst)
            cur_a = [x for x in mine.values() if x.side == "ask"]
            if (
                self.USE_AMEND
                and want_a
                and ot_a == "limit"
                and len(cur_a) == 1
                and abs(cur_a[0].price - want_a[0]) < EPS
                and 0 < want_a[1] < cur_a[0].volume
                and (want_b is None or cur_a[0].price > want_b[0] + EPS)
            ):
                if self.spend(reserve):
                    self.ex.amend_order(inst, order_id=cur_a[0].order_id, volume=int(want_a[1]))
            else:
                for o in cur_a:
                    if not self.spend(reserve):
                        return
                    self.ex.delete_order(inst, order_id=o.order_id)
                if want_a:
                    pos = int(self.ex.get_positions().get(inst, 0))
                    mine = self.ex.get_outstanding_orders(inst)
                    if not any(o.side == "bid" and o.price >= want_a[0] - EPS for o in mine.values()):
                        open_a = sum(o.volume for o in mine.values() if o.side == "ask")
                        open_tot = sum(o.volume for o in mine.values())
                        v = min(want_a[1], POS_LIMIT + pos - open_a, MAX_OPEN_VOL - open_tot)
                        if v > 0 and self.spend(0 if ot_a == "ioc" else reserve):
                            self.ex.insert_order(inst, price=want_a[0], volume=int(v), side="ask", order_type=ot_a)
