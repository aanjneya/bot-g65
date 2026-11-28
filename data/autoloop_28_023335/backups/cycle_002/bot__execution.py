"""Risk limits, tick rounding, and order-book execution for Optibook."""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from types import SimpleNamespace
from typing import Any, Deque, Dict, Optional, Set, Tuple

from bot.signals import find_protecting_wall

logger = logging.getLogger(__name__)


def strip_own_orders(book: Any, open_orders: Optional[Dict[int, Any]], tick_size: float = 0.10) -> Any:
    """
    Return a copy of PriceBook with our own resting limit orders subtracted.
    Prevents self-feedback loops in mid, spread, Microprice, OFI, wall detection, and BBO clamping.
    """
    if book is None or not open_orders:
        return book

    tick = float(tick_size if tick_size and tick_size > 0 else 0.10)
    own_bid: Dict[int, int] = {}
    own_ask: Dict[int, int] = {}
    for o in open_orders.values():
        key = int(round(float(o.price) / tick))
        vol = int(o.volume)
        if o.side == "bid":
            own_bid[key] = own_bid.get(key, 0) + vol
        elif o.side == "ask":
            own_ask[key] = own_ask.get(key, 0) + vol

    clean_bids = []
    for lv in getattr(book, "bids", None) or []:
        p = float(lv.price)
        rem = int(lv.volume) - own_bid.get(int(round(p / tick)), 0)
        if rem > 0:
            clean_bids.append(SimpleNamespace(price=p, volume=rem))

    clean_asks = []
    for lv in getattr(book, "asks", None) or []:
        p = float(lv.price)
        rem = int(lv.volume) - own_ask.get(int(round(p / tick)), 0)
        if rem > 0:
            clean_asks.append(SimpleNamespace(price=p, volume=rem))

    return SimpleNamespace(
        instrument_id=getattr(book, "instrument_id", None),
        timestamp=getattr(book, "timestamp", None),
        bids=clean_bids,
        asks=clean_asks,
    )


def round_to_tick(price: float, tick_size: float, side: str) -> float:
    """Round price conservatively to the exchange tick grid (floor bids, ceil asks)."""
    if tick_size <= 0:
        return round(price, 2)
    ratio = price / tick_size
    if side == "bid":
        steps = math.floor(ratio + 1e-9)
    elif side == "ask":
        steps = math.ceil(ratio - 1e-9)
    else:
        steps = round(ratio)
    rounded = steps * tick_size
    # Avoid floating point representation artifacts like 44.60000000000001
    decimals = max(0, len(f"{tick_size:.10f}".rstrip("0").split(".")[-1]))
    return round(rounded, decimals)


def clamp_price(price: float, instrument: Any) -> float:
    """Clamp order price within Instrument.price_limit if configured."""
    limit = getattr(instrument, "price_limit", None)
    if limit is None:
        return price
    return max(float(limit.min), min(float(limit.max), price))


def best_bid_ask(book: Any) -> Tuple[Optional[float], Optional[int], Optional[float], Optional[int]]:
    """Extract (best_bid, bid_vol, best_ask, ask_vol) from a PriceBook."""
    bids = getattr(book, "bids", None) or []
    asks = getattr(book, "asks", None) or []
    bid_p = float(bids[0].price) if bids else None
    bid_v = int(bids[0].volume) if bids else None
    ask_p = float(asks[0].price) if asks else None
    ask_v = int(asks[0].volume) if asks else None
    return bid_p, bid_v, ask_p, ask_v


def mid_price(book: Any) -> Optional[float]:
    """Return mid-price from a PriceBook, or None if one side is empty."""
    bid_p, _, ask_p, _ = best_bid_ask(book)
    if bid_p is None or ask_p is None:
        return None
    return 0.5 * (bid_p + ask_p)


def clamp_delta_for_limit(
    desired_delta: int,
    current_pos: int,
    open_bid_vol: int = 0,
    open_ask_vol: int = 0,
    max_pos: int = 100,
) -> int:
    """
    Clamp an order's signed volume so Optibook's hard +/-max_pos limit is never breached.
    Accounts for both current position and resting open orders on that side.
    """
    if desired_delta > 0:
        headroom = max_pos - (current_pos + open_bid_vol)
        return max(0, min(desired_delta, headroom))
    if desired_delta < 0:
        headroom = max_pos + (current_pos - open_ask_vol)
        return -max(0, min(-desired_delta, headroom))
    return 0


class RiskAndExecution:
    """Executes IOC rebalances, news snipes, and queue-preserving passive limit quotes."""

    def __init__(
        self,
        exchange: Any,
        max_position_lots: int = 100,
        max_open_order_volume: int = 180,
        max_requests_per_sec: int = 20,
    ) -> None:
        self.exchange = exchange
        self.max_position_lots = max_position_lots
        self.max_open_order_volume = max_open_order_volume
        self.max_requests_per_sec = max_requests_per_sec
        self._req_times: Deque[float] = deque()

    def _can_send_request(self, reserve: int = 0) -> bool:
        now = time.monotonic()
        while self._req_times and now - self._req_times[0] >= 1.0:
            self._req_times.popleft()
        cap = max(1, self.max_requests_per_sec - reserve)
        return len(self._req_times) < cap

    def _record_request(self) -> None:
        self._req_times.append(time.monotonic())

    def _safe_delete_all(self, inst_id: str) -> bool:
        """Cancel all outstanding orders on inst_id using full priority budget."""
        if not self._can_send_request(reserve=0):
            return False
        self._record_request()
        try:
            self.exchange.delete_orders(inst_id)
            return True
        except Exception as exc:
            logger.warning("delete_orders exception on %s: %s", inst_id, exc)
            return False

    def execute_target_positions(
        self,
        targets: Dict[str, int],
        books: Dict[str, Any],
        instruments: Dict[str, Any],
    ) -> int:
        """Send IOC orders to move current positions toward targets. Returns number of orders sent."""
        if not self.exchange.is_connected():
            return 0

        positions = self.exchange.get_positions()
        orders_sent = 0

        for inst_id, target_pos in targets.items():
            if not self.exchange.is_connected():
                break
            target_clamped = max(-self.max_position_lots, min(self.max_position_lots, int(target_pos)))
            cur_pos = int(positions.get(inst_id, 0))
            raw_delta = target_clamped - cur_pos
            if raw_delta == 0:
                continue

            book = books.get(inst_id)
            inst = instruments.get(inst_id)
            if book is None or inst is None:
                continue

            open_orders = self.exchange.get_outstanding_orders(inst_id)
            open_bid = sum(o.volume for o in open_orders.values() if o.side == "bid")
            open_ask = sum(o.volume for o in open_orders.values() if o.side == "ask")

            delta = clamp_delta_for_limit(
                raw_delta,
                current_pos=cur_pos,
                open_bid_vol=open_bid,
                open_ask_vol=open_ask,
                max_pos=self.max_position_lots,
            )
            if delta == 0:
                continue

            bid_p, bid_v, ask_p, ask_v = best_bid_ask(book)
            tick = float(getattr(inst, "tick_size", 0.01) or 0.01)

            if delta > 0:
                if ask_p is None or ask_v is None or ask_v <= 0:
                    continue
                vol = min(delta, ask_v)
                price = clamp_price(round_to_tick(ask_p, tick, "bid"), inst)
                side = "bid"
                conflicting = [o for o in open_orders.values() if o.side == "ask" and o.price <= price]
            else:
                if bid_p is None or bid_v is None or bid_v <= 0:
                    continue
                vol = min(-delta, bid_v)
                price = clamp_price(round_to_tick(bid_p, tick, "ask"), inst)
                side = "ask"
                conflicting = [o for o in open_orders.values() if o.side == "bid" and o.price >= price]

            if vol <= 0:
                continue

            # CRITICAL (Optibook rule): Crossing our own resting limit order triggers a forced disconnect.
            # Cancel any conflicting opposite-side resting orders before sending an aggressive IOC.
            if conflicting:
                if not self._safe_delete_all(inst_id):
                    continue
                # Refresh book after cancelling our own quotes so we don't trade against stale prices
                book = self.exchange.get_last_price_book(inst_id)
                bid_p, bid_v, ask_p, ask_v = best_bid_ask(book)
                if side == "bid":
                    if ask_p is None or ask_v is None or ask_v <= 0:
                        continue
                    vol = min(delta, ask_v)
                    price = clamp_price(round_to_tick(ask_p, tick, "bid"), inst)
                else:
                    if bid_p is None or bid_v is None or bid_v <= 0:
                        continue
                    vol = min(-delta, bid_v)
                    price = clamp_price(round_to_tick(bid_p, tick, "ask"), inst)

            if vol <= 0 or not self._can_send_request(reserve=0):
                continue

            self._record_request()
            try:
                resp = self.exchange.insert_order(
                    inst_id,
                    price=price,
                    volume=vol,
                    side=side,
                    order_type="ioc",
                )
            except Exception as exc:
                logger.warning("Order exception on %s (%s %s@%s): %s", inst_id, side, vol, price, exc)
                continue
            if resp.success:
                orders_sent += 1
            else:
                logger.warning("Order rejected on %s (%s %s@%s): %s", inst_id, side, vol, price, resp.error_reason)

        return orders_sent

    def flatten_positions(
        self,
        books: Dict[str, Any],
        instruments: Dict[str, Any],
    ) -> int:
        """Endgame flattener: cancel open orders and send risk-reducing IOCs toward 0."""
        if not self.exchange.is_connected():
            return 0

        positions = self.exchange.get_positions()
        # First cancel all resting quotes so we don't accidentally build new inventory
        for inst_id in instruments:
            if not self.exchange.is_connected():
                return 0
            open_orders = self.exchange.get_outstanding_orders(inst_id)
            if open_orders:
                self._safe_delete_all(inst_id)

        targets = {inst_id: 0 for inst_id, pos in positions.items() if int(pos) != 0 and inst_id in instruments}
        if not targets:
            return 0
        return self.execute_target_positions(targets, books, instruments)

    def execute_news_snipes(
        self,
        news_snipes: Dict[str, Tuple[str, int]],
        books: Dict[str, Any],
        instruments: Dict[str, Any],
    ) -> Set[str]:
        """
        Execute high-priority bounded IOC snipes on unambiguous news sentiment.
        Always cancels any resting orders on that instrument first (zero self-trade).
        """
        if not news_snipes or not self.exchange.is_connected():
            return set()

        positions = self.exchange.get_positions()
        sniped: Set[str] = set()

        for inst_id, (side, max_lots) in news_snipes.items():
            if not self.exchange.is_connected():
                break
            book = books.get(inst_id)
            inst = instruments.get(inst_id)
            if book is None or inst is None or max_lots <= 0:
                continue

            # Always pull resting quotes on news-hit tickers first
            open_orders = self.exchange.get_outstanding_orders(inst_id)
            if open_orders:
                if not self._safe_delete_all(inst_id):
                    continue
                book = self.exchange.get_last_price_book(inst_id)

            bid_p, bid_v, ask_p, ask_v = best_bid_ask(book)
            tick = float(getattr(inst, "tick_size", 0.01) or 0.01)
            cur_pos = int(positions.get(inst_id, 0))

            if side == "bid":
                if ask_p is None or ask_v is None or ask_v <= 0:
                    continue
                desired = clamp_delta_for_limit(max_lots, cur_pos, 0, 0, self.max_position_lots)
                vol = min(desired, ask_v)
                price = clamp_price(round_to_tick(ask_p, tick, "bid"), inst)
            elif side == "ask":
                if bid_p is None or bid_v is None or bid_v <= 0:
                    continue
                desired = clamp_delta_for_limit(-max_lots, cur_pos, 0, 0, self.max_position_lots)
                vol = min(-desired, bid_v)
                price = clamp_price(round_to_tick(bid_p, tick, "ask"), inst)
            else:
                continue

            if vol <= 0 or not self._can_send_request(reserve=0):
                continue

            self._record_request()
            try:
                resp = self.exchange.insert_order(
                    inst_id,
                    price=price,
                    volume=vol,
                    side=side,
                    order_type="ioc",
                )
                if resp.success:
                    sniped.add(inst_id)
                else:
                    logger.warning("News IOC rejected on %s (%s %s@%s): %s", inst_id, side, vol, price, resp.error_reason)
            except Exception as exc:
                logger.warning("News IOC exception on %s: %s", inst_id, exc)

        return sniped

    def execute_fair_value_edge(
        self,
        fair_values: Dict[str, float],
        books: Dict[str, Any],
        instruments: Dict[str, Any],
        paused_instruments: Set[str],
        max_edge_lots: int = 10,
        min_edge_ticks: int = 3,
    ) -> Set[str]:
        """
        Send aggressive IOC orders ONLY when the live book is mispriced beyond fair value
        by at least max(min_edge_ticks * tick, 1.5 * spread).
        Cancels any resting orders on that instrument first and returns the set of traded
        instruments (so passive quoting skips them this tick, preventing self-trades).
        """
        if not self.exchange.is_connected():
            return set()

        positions = self.exchange.get_positions()
        traded: Set[str] = set()

        for inst_id, fv in fair_values.items():
            if inst_id in paused_instruments or not self.exchange.is_connected():
                continue

            book = books.get(inst_id)
            inst = instruments.get(inst_id)
            if book is None or inst is None:
                continue

            bid_p, bid_v, ask_p, ask_v = best_bid_ask(book)
            if bid_p is None or ask_p is None:
                continue

            tick = float(getattr(inst, "tick_size", 0.10) or 0.10)
            spread = max(ask_p - bid_p, tick)
            min_edge = max(min_edge_ticks * tick, 1.5 * spread)
            cur_pos = int(positions.get(inst_id, 0))

            if ask_p <= fv - min_edge and ask_v and ask_v > 0:
                side = "bid"
                desired_delta = clamp_delta_for_limit(max_edge_lots, cur_pos, 0, 0, self.max_position_lots)
                vol = min(desired_delta, ask_v)
                price = clamp_price(round_to_tick(ask_p, tick, "bid"), inst)
            elif bid_p >= fv + min_edge and bid_v and bid_v > 0:
                side = "ask"
                desired_delta = clamp_delta_for_limit(-max_edge_lots, cur_pos, 0, 0, self.max_position_lots)
                vol = min(-desired_delta, bid_v)
                price = clamp_price(round_to_tick(bid_p, tick, "ask"), inst)
            else:
                continue

            if vol <= 0:
                continue

            open_orders = self.exchange.get_outstanding_orders(inst_id)
            if open_orders:
                if self._safe_delete_all(inst_id):
                    # Mark as traded and wait until next tick to avoid racing our own cancel
                    traded.add(inst_id)
                continue

            if not self._can_send_request(reserve=0):
                continue

            self._record_request()
            try:
                resp = self.exchange.insert_order(
                    inst_id,
                    price=price,
                    volume=vol,
                    side=side,
                    order_type="ioc",
                )
                if resp.success:
                    traded.add(inst_id)
                else:
                    logger.warning("Edge IOC rejected on %s (%s %s@%s): %s", inst_id, side, vol, price, resp.error_reason)
            except Exception as exc:
                logger.warning("Edge IOC exception on %s: %s", inst_id, exc)

        return traded

    def _sync_passive_quotes(
        self,
        inst_id: str,
        inst: Any,
        target_bid: float,
        target_ask: float,
        bid_vol: int,
        ask_vol: int,
        open_orders: Dict[int, Any],
        total_open_vol: int,
        passive_reserve: int,
    ) -> Tuple[int, int]:
        """
        Queue-preserving per-side order sync for a single instrument.
        Returns (orders_sent, updated_total_open_vol).
        Guarantees zero self-trades even under partial rate-limit throttling.
        """
        tick = float(getattr(inst, "tick_size", 0.10) or 0.10)
        orders_sent = 0

        open_bids = [o for o in open_orders.values() if o.side == "bid"]
        open_asks = [o for o in open_orders.values() if o.side == "ask"]

        bid_price_ok = len(open_bids) == 1 and abs(open_bids[0].price - target_bid) < tick * 0.5
        ask_price_ok = len(open_asks) == 1 and abs(open_asks[0].price - target_ask) < tick * 0.5
        bid_vol_ok = bid_price_ok and open_bids[0].volume == bid_vol
        ask_vol_ok = ask_price_ok and open_asks[0].volume == ask_vol

        bid_done = (bid_vol == 0 and not open_bids) or bid_vol_ok
        ask_done = (ask_vol == 0 and not open_asks) or ask_vol_ok
        if bid_done and ask_done:
            return 0, total_open_vol

        need_bid_amend = (
            not bid_done
            and bid_price_ok
            and bid_vol > 0
            and open_bids[0].volume > bid_vol
            and hasattr(self.exchange, "amend_order")
        )
        need_ask_amend = (
            not ask_done
            and ask_price_ok
            and ask_vol > 0
            and open_asks[0].volume > ask_vol
            and hasattr(self.exchange, "amend_order")
        )

        # Phase 1: Cancel stale orders BEFORE inserting new ones (prevents self-trade if BBO shifted)
        remaining_bids = list(open_bids)
        remaining_asks = list(open_asks)

        if not bid_done and not need_bid_amend:
            still_bids = []
            for ob in open_bids:
                if not self._can_send_request(passive_reserve):
                    still_bids.append(ob)
                    continue
                self._record_request()
                try:
                    r_del = self.exchange.delete_order(inst_id, order_id=ob.order_id)
                    if getattr(r_del, "success", True):
                        total_open_vol = max(0, total_open_vol - int(ob.volume))
                    else:
                        still_bids.append(ob)
                except Exception as exc:
                    logger.warning("Delete bid exception on %s: %s", inst_id, exc)
                    still_bids.append(ob)
            remaining_bids = still_bids

        if not ask_done and not need_ask_amend:
            still_asks = []
            for oa in open_asks:
                if not self._can_send_request(passive_reserve):
                    still_asks.append(oa)
                    continue
                self._record_request()
                try:
                    r_del = self.exchange.delete_order(inst_id, order_id=oa.order_id)
                    if getattr(r_del, "success", True):
                        total_open_vol = max(0, total_open_vol - int(oa.volume))
                    else:
                        still_asks.append(oa)
                except Exception as exc:
                    logger.warning("Delete ask exception on %s: %s", inst_id, exc)
                    still_asks.append(oa)
            remaining_asks = still_asks

        # Phase 2: Amend or insert updated sides (strictly guarded against self-trade & volume cap)
        if need_bid_amend and self._can_send_request(passive_reserve):
            self._record_request()
            try:
                old_vol = int(open_bids[0].volume)
                r_am = self.exchange.amend_order(inst_id, order_id=open_bids[0].order_id, volume=bid_vol)
                if r_am.success:
                    orders_sent += 1
                    total_open_vol = max(0, total_open_vol - (old_vol - bid_vol))
            except Exception as exc:
                logger.warning("Amend bid exception on %s: %s", inst_id, exc)
        elif (
            not bid_done
            and not remaining_bids
            and bid_vol > 0
            and all(oa.price > target_bid + 1e-9 for oa in remaining_asks)
            and total_open_vol + bid_vol <= self.max_open_order_volume
            and self._can_send_request(passive_reserve)
        ):
            self._record_request()
            try:
                r_bid = self.exchange.insert_order(
                    inst_id, price=target_bid, volume=bid_vol, side="bid", order_type="limit"
                )
                if r_bid.success:
                    orders_sent += 1
                    total_open_vol += bid_vol
                    remaining_bids.append(type("_B", (), {"price": target_bid, "volume": bid_vol})())
            except Exception as exc:
                logger.warning("Quote bid exception on %s: %s", inst_id, exc)

        if need_ask_amend and self._can_send_request(passive_reserve):
            self._record_request()
            try:
                old_vol = int(open_asks[0].volume)
                r_am = self.exchange.amend_order(inst_id, order_id=open_asks[0].order_id, volume=ask_vol)
                if r_am.success:
                    orders_sent += 1
                    total_open_vol = max(0, total_open_vol - (old_vol - ask_vol))
            except Exception as exc:
                logger.warning("Amend ask exception on %s: %s", inst_id, exc)
        elif (
            not ask_done
            and not remaining_asks
            and ask_vol > 0
            and all(ob.price < target_ask - 1e-9 for ob in remaining_bids)
            and total_open_vol + ask_vol <= self.max_open_order_volume
            and self._can_send_request(passive_reserve)
        ):
            self._record_request()
            try:
                r_ask = self.exchange.insert_order(
                    inst_id, price=target_ask, volume=ask_vol, side="ask", order_type="limit"
                )
                if r_ask.success:
                    orders_sent += 1
                    total_open_vol += ask_vol
            except Exception as exc:
                logger.warning("Quote ask exception on %s: %s", inst_id, exc)

        return orders_sent, total_open_vol

    def execute_scratches(
        self,
        scratches: Dict[str, str],
        books: Dict[str, Any],
        instruments: Dict[str, Any],
    ) -> Set[str]:
        """
        Immediately flatten positions whose protecting house wall broke/vanished or exceeded max_hold_sec.
        Cancels resting orders on that instrument first to guarantee zero self-trades.
        """
        if not scratches or not self.exchange.is_connected():
            return set()

        positions = self.exchange.get_positions()
        scratched: Set[str] = set()

        for inst_id, reason in scratches.items():
            if not self.exchange.is_connected():
                break
            cur_pos = int(positions.get(inst_id, 0))
            if cur_pos == 0:
                continue
            inst = instruments.get(inst_id)
            if inst is None:
                continue

            open_orders = self.exchange.get_outstanding_orders(inst_id)
            if open_orders:
                if not self._safe_delete_all(inst_id):
                    continue

            fresh_book = self.exchange.get_last_price_book(inst_id) or books.get(inst_id)
            bid_p, bid_v, ask_p, ask_v = best_bid_ask(fresh_book)
            tick = float(getattr(inst, "tick_size", 0.10) or 0.10)

            if cur_pos > 0:
                if bid_p is None:
                    continue
                side = "ask"
                # Allow 1 extra tick of slippage if wall just broke so we don't miss the exit
                price = clamp_price(round_to_tick(bid_p - tick, tick, "ask"), inst)
                vol = cur_pos
            else:
                if ask_p is None:
                    continue
                side = "bid"
                price = clamp_price(round_to_tick(ask_p + tick, tick, "bid"), inst)
                vol = -cur_pos

            if vol <= 0 or not self._can_send_request(reserve=0):
                continue

            self._record_request()
            try:
                resp = self.exchange.insert_order(
                    inst_id, price=price, volume=vol, side=side, order_type="ioc"
                )
                if resp.success:
                    logger.info("Scratched %s pos=%d (%s) via IOC %s@%.2f", inst_id, cur_pos, reason, side, price)
                    scratched.add(inst_id)
            except Exception as exc:
                logger.warning("Scratch IOC exception on %s (%s): %s", inst_id, reason, exc)

        return scratched

    def quote_from_plans(
        self,
        quote_plans: Dict[str, Any],
        books: Dict[str, Any],
        instruments: Dict[str, Any],
        paused_instruments: Set[str],
        open_orders_by_inst: Optional[Dict[str, Dict[int, Any]]] = None,
        wall_min_volume: int = 250,
    ) -> int:
        """
        Maintain passive GLFT / wall-backed limit quotes from ABMM-S QuotePlans.
        Never crosses opposite BBO, steps inside wide spreads, preserves queue priority,
        and enforces per-instrument open-order volume and +/-max_pos limits.
        """
        if not self.exchange.is_connected():
            return 0

        positions = self.exchange.get_positions()
        passive_reserve = 4 if self.max_requests_per_sec > 8 else 0
        orders_sent = 0

        # Prioritize instruments with largest inventory so risk-reducing exit updates never starve
        ordered_insts = sorted(
            instruments.items(),
            key=lambda kv: abs(int(positions.get(kv[0], 0))),
            reverse=True,
        )

        for inst_id, inst in ordered_insts:
            if not self.exchange.is_connected():
                break

            open_orders = (
                open_orders_by_inst[inst_id]
                if open_orders_by_inst is not None and inst_id in open_orders_by_inst
                else self.exchange.get_outstanding_orders(inst_id)
            )
            if inst_id in paused_instruments:
                if open_orders:
                    self._safe_delete_all(inst_id)
                continue

            plan = quote_plans.get(inst_id)
            book = books.get(inst_id)
            if plan is None or book is None:
                continue

            bid_p, _, ask_p, _ = best_bid_ask(book)
            if bid_p is None or ask_p is None:
                if open_orders:
                    self._safe_delete_all(inst_id)
                continue

            tick = float(getattr(inst, "tick_size", 0.10) or 0.10)
            res_px = float(plan.reservation_price)
            half_sp = max(tick, float(plan.half_spread))

            raw_bid = float(plan.bid_price) if getattr(plan, "bid_price", None) is not None else (res_px - half_sp)
            raw_ask = float(plan.ask_price) if getattr(plan, "ask_price", None) is not None else (res_px + half_sp)

            # Strictly passive against OTHER participants' BBO (books are pre-cleaned of own orders)
            target_bid = clamp_price(round_to_tick(min(raw_bid, ask_p - tick), tick, "bid"), inst)
            target_ask = clamp_price(round_to_tick(max(raw_ask, bid_p + tick), tick, "ask"), inst)

            cur_pos = int(positions.get(inst_id, 0))
            bid_vol = clamp_delta_for_limit(int(plan.bid_lots), cur_pos, 0, 0, self.max_position_lots)
            ask_vol = -clamp_delta_for_limit(-int(plan.ask_lots), cur_pos, 0, 0, self.max_position_lots)

            # Size entries at the price we will actually rest, not at unrounded FV.
            # Joining a wall does not leave its queue behind us if our order fills.
            probe_cap = 1 if cur_pos == 0 else 0
            if cur_pos >= 0 and bid_vol > 0:
                bid_wall = find_protecting_wall(book, "bid", tick, wall_min_volume=wall_min_volume)
                if bid_wall is None or abs(target_bid - bid_wall - tick) > 1e-8:
                    bid_vol = min(bid_vol, probe_cap)
            if cur_pos <= 0 and ask_vol > 0:
                ask_wall = find_protecting_wall(book, "ask", tick, wall_min_volume=wall_min_volume)
                if ask_wall is None or abs(ask_wall - target_ask - tick) > 1e-8:
                    ask_vol = min(ask_vol, probe_cap)

            # Compete for a passive exit without crossing the external book or flipping inventory.
            if cur_pos > 0:
                exit_ask = max(bid_p + tick, ask_p - tick)
                target_ask = min(target_ask, clamp_price(round_to_tick(exit_ask, tick, "ask"), inst))
                ask_vol = min(ask_vol, cur_pos)
            elif cur_pos < 0:
                exit_bid = min(ask_p - tick, bid_p + tick)
                target_bid = max(target_bid, clamp_price(round_to_tick(exit_bid, tick, "bid"), inst))
                bid_vol = min(bid_vol, -cur_pos)

            if target_bid >= target_ask - 1e-9:
                # Never cross ourselves: keep only the inventory-reducing side!
                if cur_pos > 0:
                    bid_vol = 0
                elif cur_pos < 0:
                    ask_vol = 0
                else:
                    bid_vol = 0
                    ask_vol = 0

            inst_open_vol = sum(int(o.volume) for o in open_orders.values())

            sent, _ = self._sync_passive_quotes(
                inst_id=inst_id,
                inst=inst,
                target_bid=target_bid,
                target_ask=target_ask,
                bid_vol=bid_vol,
                ask_vol=ask_vol,
                open_orders=open_orders,
                total_open_vol=inst_open_vol,
                passive_reserve=passive_reserve,
            )
            orders_sent += sent

        return orders_sent

    def quote_around_fair_value(
        self,
        fair_values: Dict[str, float],
        books: Dict[str, Any],
        instruments: Dict[str, Any],
        paused_instruments: Set[str],
        quote_lots: int = 5,
        edge_ticks: int = 1,
    ) -> int:
        """
        Maintain inventory-skewed passive limit quotes around each stock's anchored fair value.
        Never crosses the opposite side of the book (zero self-trades, zero spread bleed).
        """
        if not self.exchange.is_connected():
            return 0

        positions = self.exchange.get_positions()
        all_open: Dict[str, Dict[int, Any]] = {}
        total_open_vol = 0
        for inst_id in instruments:
            oo = self.exchange.get_outstanding_orders(inst_id)
            all_open[inst_id] = oo
            total_open_vol += sum(int(o.volume) for o in oo.values())

        passive_reserve = 4 if self.max_requests_per_sec > 8 else 0
        orders_sent = 0

        for inst_id, inst in instruments.items():
            if not self.exchange.is_connected():
                break

            open_orders = all_open.get(inst_id, {})

            if inst_id in paused_instruments:
                if open_orders and self._safe_delete_all(inst_id):
                    total_open_vol = max(0, total_open_vol - sum(int(o.volume) for o in open_orders.values()))
                continue

            fv = fair_values.get(inst_id)
            book = books.get(inst_id)
            if fv is None or book is None:
                continue

            bid_p, _, ask_p, _ = best_bid_ask(book)
            if bid_p is None or ask_p is None:
                continue

            tick = float(getattr(inst, "tick_size", 0.10) or 0.10)
            mid = 0.5 * (bid_p + ask_p)
            anchored_fv = max(mid - 3.0 * tick, min(mid + 3.0 * tick, 0.5 * mid + 0.5 * fv))
            cur_pos = int(positions.get(inst_id, 0))

            skew = -(cur_pos / max(self.max_position_lots, 1)) * tick * 4.0
            edge = max(1, edge_ticks) * tick

            target_bid = clamp_price(round_to_tick(min(anchored_fv + skew - edge, ask_p - tick), tick, "bid"), inst)
            target_ask = clamp_price(round_to_tick(max(anchored_fv + skew + edge, bid_p + tick), tick, "ask"), inst)
            if target_bid >= target_ask:
                continue

            raw_bid_lots = max(1, quote_lots // 2) if cur_pos > 15 else quote_lots
            raw_ask_lots = max(1, quote_lots // 2) if cur_pos < -15 else quote_lots
            bid_vol = clamp_delta_for_limit(raw_bid_lots, cur_pos, 0, 0, self.max_position_lots)
            ask_vol = -clamp_delta_for_limit(-raw_ask_lots, cur_pos, 0, 0, self.max_position_lots)

            sent, total_open_vol = self._sync_passive_quotes(
                inst_id=inst_id,
                inst=inst,
                target_bid=target_bid,
                target_ask=target_ask,
                bid_vol=bid_vol,
                ask_vol=ask_vol,
                open_orders=open_orders,
                total_open_vol=total_open_vol,
                passive_reserve=passive_reserve,
            )
            orders_sent += sent

        return orders_sent
