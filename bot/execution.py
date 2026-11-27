"""Risk limits, tick rounding, and order-book execution for Optibook."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

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
    """Executes target leg positions via IOC orders while enforcing Optibook position limits."""

    def __init__(self, exchange: Any, max_position_lots: int = 100) -> None:
        self.exchange = exchange
        self.max_position_lots = max_position_lots

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
            else:
                if bid_p is None or bid_v is None or bid_v <= 0:
                    continue
                vol = min(-delta, bid_v)
                price = clamp_price(round_to_tick(bid_p, tick, "ask"), inst)
                side = "ask"

            if vol <= 0:
                continue

            resp = self.exchange.insert_order(
                inst_id,
                price=price,
                volume=vol,
                side=side,
                order_type="ioc",
            )
            if resp.success:
                orders_sent += 1
            else:
                logger.warning("Order rejected on %s (%s %s@%s): %s", inst_id, side, vol, price, resp.error_reason)

        return orders_sent
