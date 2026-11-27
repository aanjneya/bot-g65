"""Risk limits, tick rounding, and order-book execution for Optibook."""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any, Deque, Dict, Optional, Set, Tuple

logger = logging.getLogger(__name__)


def round_to_tick(price: float, tick_size: float, side: str) -> float:
    """Round price to the nearest valid exchange tick (aggressive or passive)."""
    if tick_size <= 0:
        return round(price, 2)
    steps = round(price / tick_size)
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
    """Executes IOC rebalances and passive limit quotes while enforcing Optibook limits."""

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

    def _can_send_request(self) -> bool:
        now = time.monotonic()
        while self._req_times and now - self._req_times[0] >= 1.0:
            self._req_times.popleft()
        return len(self._req_times) < self.max_requests_per_sec

    def _record_request(self) -> None:
        self._req_times.append(time.monotonic())

    def execute_target_positions(
        self,
        targets: Dict[str, int],
        books: Dict[str, Any],
        instruments: Dict[str, Any],
    ) -> int:
        """Send IOC orders to move current positions toward targets. Returns number of orders sent."""
        positions = self.exchange.get_positions()
        orders_sent = 0

        for inst_id, target_pos in targets.items():
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
            tick = float(getattr(inst, "tick_size", 0.01))

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
                if not self._can_send_request():
                    continue
                self._record_request()
                self.exchange.delete_orders(inst_id)
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

            if vol <= 0 or not self._can_send_request():
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
        Maintain inventory-skewed passive limit quotes around each stock's Kalman fair value.
        Cancels quotes on paused instruments and avoids crossing the spread.
        """
        positions = self.exchange.get_positions()
        orders_sent = 0

        for inst_id, inst in instruments.items():
            open_orders = self.exchange.get_outstanding_orders(inst_id)

            if inst_id in paused_instruments:
                if open_orders:
                    self._record_request()
                    self.exchange.delete_orders(inst_id)
                continue

            fv = fair_values.get(inst_id)
            book = books.get(inst_id)
            if fv is None or book is None:
                continue

            bid_p, _, ask_p, _ = best_bid_ask(book)
            if bid_p is None or ask_p is None:
                continue

            tick = float(getattr(inst, "tick_size", 0.10))
            cur_pos = int(positions.get(inst_id, 0))

            # Inventory skew: shift fair value down when long, up when short
            skew = -(cur_pos / max(self.max_position_lots, 1)) * tick * 2.0
            edge = max(1, edge_ticks) * tick

            target_bid = round_to_tick(min(fv + skew - edge, ask_p - tick), tick, "bid")
            target_ask = round_to_tick(max(fv + skew + edge, bid_p + tick), tick, "ask")
            target_bid = clamp_price(target_bid, inst)
            target_ask = clamp_price(target_ask, inst)

            if target_bid >= target_ask:
                continue

            open_bids = [o for o in open_orders.values() if o.side == "bid"]
            open_asks = [o for o in open_orders.values() if o.side == "ask"]
            open_bid_vol = sum(o.volume for o in open_bids)
            open_ask_vol = sum(o.volume for o in open_asks)

            bid_stale = (not open_bids) or any(abs(o.price - target_bid) >= tick * 0.5 for o in open_bids)
            ask_stale = (not open_asks) or any(abs(o.price - target_ask) >= tick * 0.5 for o in open_asks)

            if not (bid_stale or ask_stale):
                continue

            # Check open volume & position headroom before replacing quotes
            bid_vol = clamp_delta_for_limit(quote_lots, cur_pos, 0, 0, self.max_position_lots)
            ask_vol = -clamp_delta_for_limit(-quote_lots, cur_pos, 0, 0, self.max_position_lots)
            if open_bid_vol + open_ask_vol > self.max_open_order_volume:
                bid_vol = 0
                ask_vol = 0

            if open_orders:
                if not self._can_send_request():
                    continue
                self._record_request()
                self.exchange.delete_orders(inst_id)

            if bid_vol > 0 and self._can_send_request():
                self._record_request()
                r_bid = self.exchange.insert_order(inst_id, price=target_bid, volume=bid_vol, side="bid", order_type="limit")
                if r_bid.success:
                    orders_sent += 1

            if ask_vol > 0 and self._can_send_request():
                self._record_request()
                r_ask = self.exchange.insert_order(inst_id, price=target_ask, volume=ask_vol, side="ask", order_type="limit")
                if r_ask.success:
                    orders_sent += 1

        return orders_sent
