"""Risk limits, tick rounding, and order-book execution for Optibook."""

from __future__ import annotations

import logging
import math
import time
from collections import deque
from typing import Any, Deque, Dict, Optional, Set, Tuple

logger = logging.getLogger(__name__)


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

    def _can_send_request(self, reserve: int = 0) -> bool:
        now = time.monotonic()
        while self._req_times and now - self._req_times[0] >= 1.0:
            self._req_times.popleft()
        cap = max(1, self.max_requests_per_sec - reserve)
        return len(self._req_times) < cap

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

            tick = float(getattr(inst, "tick_size", 0.10))
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
                if not self._can_send_request():
                    continue
                self._record_request()
                self.exchange.delete_orders(inst_id)
                # Mark as traded and wait until next tick to avoid racing our own cancel
                traded.add(inst_id)
                continue

            if not self._can_send_request():
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
        orders_sent = 0

        for inst_id, inst in instruments.items():
            if not self.exchange.is_connected():
                break

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
            mid = 0.5 * (bid_p + ask_p)
            # Anchor fair value near live mid so slow Kalman drift never gets picked off
            anchored_fv = max(mid - 3.0 * tick, min(mid + 3.0 * tick, 0.5 * mid + 0.5 * fv))
            cur_pos = int(positions.get(inst_id, 0))

            # Strong inventory skew: push quotes down when long, up when short
            skew = -(cur_pos / max(self.max_position_lots, 1)) * tick * 4.0
            edge = max(1, edge_ticks) * tick

            # Strictly passive (never crosses opposite BBO), but CAN step inside wide spreads
            # so we sit in front of house-bot walls rather than behind them.
            target_bid = round_to_tick(min(anchored_fv + skew - edge, ask_p - tick), tick, "bid")
            target_ask = round_to_tick(max(anchored_fv + skew + edge, bid_p + tick), tick, "ask")
            target_bid = clamp_price(target_bid, inst)
            target_ask = clamp_price(target_ask, inst)

            if target_bid >= target_ask:
                continue

            open_bids = [o for o in open_orders.values() if o.side == "bid"]
            open_asks = [o for o in open_orders.values() if o.side == "ask"]
            open_bid_vol = sum(o.volume for o in open_bids)
            open_ask_vol = sum(o.volume for o in open_asks)

            # Reduce size on the inventory-increasing side when holding a position
            raw_bid_lots = max(1, quote_lots // 2) if cur_pos > 15 else quote_lots
            raw_ask_lots = max(1, quote_lots // 2) if cur_pos < -15 else quote_lots
            bid_vol = clamp_delta_for_limit(raw_bid_lots, cur_pos, 0, 0, self.max_position_lots)
            ask_vol = -clamp_delta_for_limit(-raw_ask_lots, cur_pos, 0, 0, self.max_position_lots)

            bid_price_ok = len(open_bids) == 1 and abs(open_bids[0].price - target_bid) < tick * 0.5
            ask_price_ok = len(open_asks) == 1 and abs(open_asks[0].price - target_ask) < tick * 0.5
            bid_vol_ok = bid_price_ok and open_bids[0].volume == bid_vol
            ask_vol_ok = ask_price_ok and open_asks[0].volume == ask_vol

            if (bid_vol == 0 and not open_bids or bid_vol_ok) and (ask_vol == 0 and not open_asks or ask_vol_ok):
                continue

            # Reserve 4 req/s for high-priority news/edge IOCs when budget allows
            passive_reserve = 4 if self.max_requests_per_sec > 8 else 0
            bid_done = bid_vol == 0 and not open_bids or bid_vol_ok
            ask_done = ask_vol == 0 and not open_asks or ask_vol_ok
            need_bid_amend = not bid_done and bid_price_ok and bid_vol > 0 and open_bids[0].volume > bid_vol and hasattr(self.exchange, "amend_order")
            need_ask_amend = not ask_done and ask_price_ok and ask_vol > 0 and open_asks[0].volume > ask_vol and hasattr(self.exchange, "amend_order")

            # Phase 1: Cancel/amend stale orders BEFORE inserting new ones (prevents self-trade if BBO shifted)
            if not bid_done and not need_bid_amend:
                for ob in open_bids:
                    if not self._can_send_request(passive_reserve):
                        break
                    self._record_request()
                    self.exchange.delete_order(inst_id, order_id=ob.order_id)

            if not ask_done and not need_ask_amend:
                for oa in open_asks:
                    if not self._can_send_request(passive_reserve):
                        break
                    self._record_request()
                    self.exchange.delete_order(inst_id, order_id=oa.order_id)

            # Phase 2: Amend or insert updated sides
            if need_bid_amend and self._can_send_request(passive_reserve):
                self._record_request()
                try:
                    r_am = self.exchange.amend_order(inst_id, order_id=open_bids[0].order_id, volume=bid_vol)
                    if r_am.success:
                        orders_sent += 1
                except Exception as exc:
                    logger.warning("Amend bid exception on %s: %s", inst_id, exc)
            elif not bid_done and bid_vol > 0 and open_bid_vol + open_ask_vol <= self.max_open_order_volume and self._can_send_request(passive_reserve):
                self._record_request()
                try:
                    r_bid = self.exchange.insert_order(inst_id, price=target_bid, volume=bid_vol, side="bid", order_type="limit")
                    if r_bid.success:
                        orders_sent += 1
                except Exception as exc:
                    logger.warning("Quote bid exception on %s: %s", inst_id, exc)

            if need_ask_amend and self._can_send_request(passive_reserve):
                self._record_request()
                try:
                    r_am = self.exchange.amend_order(inst_id, order_id=open_asks[0].order_id, volume=ask_vol)
                    if r_am.success:
                        orders_sent += 1
                except Exception as exc:
                    logger.warning("Amend ask exception on %s: %s", inst_id, exc)
            elif not ask_done and ask_vol > 0 and open_bid_vol + open_ask_vol <= self.max_open_order_volume and self._can_send_request(passive_reserve):
                self._record_request()
                try:
                    r_ask = self.exchange.insert_order(inst_id, price=target_ask, volume=ask_vol, side="ask", order_type="limit")
                    if r_ask.success:
                        orders_sent += 1
                except Exception as exc:
                    logger.warning("Quote ask exception on %s: %s", inst_id, exc)

        return orders_sent
