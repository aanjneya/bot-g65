"""Deterministic MockExchange matching optibook.synchronous_client.Exchange for MOCK_MODE=true."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np


@dataclass
class MockPriceVolume:
    price: float
    volume: int


@dataclass
class MockPriceBook:
    instrument_id: str
    bids: List[MockPriceVolume] = field(default_factory=list)
    asks: List[MockPriceVolume] = field(default_factory=list)


@dataclass
class MockInstrument:
    instrument_id: str
    tick_size: float = 0.01
    price_limit: Optional[object] = None


@dataclass
class MockOrderResponse:
    success: bool
    order_id: Optional[int] = None
    error_reason: Optional[str] = None


@dataclass
class MockOrderStatus:
    order_id: int
    instrument_id: str
    price: float
    volume: int
    side: str


@dataclass
class MockSocialMediaFeed:
    post: str
    meta_data: Optional[dict] = None


class MockExchange:
    """In-memory cointegrated order-book simulator for local testing and MOCK_MODE=true."""

    def __init__(self, universe: Optional[List[str]] = None, seed: int = 42, max_pos: int = 100) -> None:
        self.universe = universe or ["AMD", "AVGO", "CRWV", "MU", "NBIS", "NVDA", "SNDK", "VRT"]
        self.rng = np.random.default_rng(seed)
        self.max_pos = max_pos
        self._connected = False
        self._positions: Dict[str, int] = {inst: 0 for inst in self.universe}
        self._cash: float = 0.0
        self._outstanding: Dict[str, Dict[int, MockOrderStatus]] = {inst: {} for inst in self.universe}
        self._social_feeds: List[MockSocialMediaFeed] = []
        self._next_order_id = 1
        self._step = 0

        # Base prices and latent factor so pairs like (NVDA, AMD) and (AVGO, MU) are cointegrated
        self._base = {
            "NVDA": 130.0,
            "AMD": 140.0,
            "AVGO": 170.0,
            "MU": 100.0,
            "CRWV": 55.0,
            "NBIS": 35.0,
            "SNDK": 45.0,
            "VRT": 110.0,
        }
        self._prices: Dict[str, float] = {k: self._base.get(k, 100.0) for k in self.universe}
        self._factor = 0.0
        self._spreads = {k: 0.0 for k in self.universe}

    def connect(self) -> None:
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    def step_market(self) -> None:
        """Advance synthetic cointegrated market by one tick."""
        self._step += 1
        self._factor += float(self.rng.normal(0.0, 0.15))
        for i, inst in enumerate(self.universe):
            # Mean-reverting idiosyncratic noise (OU process)
            theta = 0.25
            sigma = 0.20 if i % 2 == 0 else 0.12
            self._spreads[inst] = (1.0 - theta) * self._spreads[inst] + float(self.rng.normal(0.0, sigma))
            beta = 1.0 + 0.1 * (i % 3)
            px = self._base.get(inst, 100.0) + beta * self._factor + self._spreads[inst]
            self._prices[inst] = max(1.0, round(px, 2))

    def get_tradable_instruments(self) -> Dict[str, MockInstrument]:
        return {inst: MockInstrument(instrument_id=inst, tick_size=0.01) for inst in self.universe}

    def get_last_price_book(self, instrument_id: str) -> MockPriceBook:
        mid = self._prices.get(instrument_id, 100.0)
        bid = round(mid - 0.01, 2)
        ask = round(mid + 0.01, 2)
        return MockPriceBook(
            instrument_id=instrument_id,
            bids=[MockPriceVolume(price=bid, volume=50), MockPriceVolume(price=round(bid - 0.02, 2), volume=100)],
            asks=[MockPriceVolume(price=ask, volume=50), MockPriceVolume(price=round(ask + 0.02, 2), volume=100)],
        )

    def get_positions(self) -> Dict[str, int]:
        return dict(self._positions)

    def get_cash(self) -> float:
        return self._cash

    def get_pnl(self, valuations: Optional[Dict[str, float]] = None) -> float:
        vals = valuations or self._prices
        mark = sum(pos * vals.get(inst, self._prices.get(inst, 0.0)) for inst, pos in self._positions.items())
        return self._cash + mark

    def get_outstanding_orders(self, instrument_id: str) -> Dict[int, MockOrderStatus]:
        return dict(self._outstanding.get(instrument_id, {}))

    def delete_orders(self, instrument_id: str) -> None:
        self._outstanding[instrument_id] = {}

    def push_mock_news(self, post: str) -> None:
        """Inject a mock social media post for testing news-feed jump guards."""
        self._social_feeds.append(MockSocialMediaFeed(post=post))

    def poll_new_social_media_feeds(self) -> List[MockSocialMediaFeed]:
        feeds = list(self._social_feeds)
        self._social_feeds.clear()
        return feeds

    def insert_order(
        self,
        instrument_id: str,
        *,
        price: float,
        volume: int,
        side: str,
        order_type: str = "limit",
    ) -> MockOrderResponse:
        if not self._connected:
            return MockOrderResponse(success=False, error_reason="Not connected")
        if volume <= 0:
            return MockOrderResponse(success=False, error_reason="Volume must be positive")

        cur = self._positions.get(instrument_id, 0)
        signed = volume if side == "bid" else -volume
        if abs(cur + signed) > self.max_pos:
            return MockOrderResponse(success=False, error_reason="Position limit exceeded")

        book = self.get_last_price_book(instrument_id)
        oid = self._next_order_id
        self._next_order_id += 1

        if side == "bid" and book.asks and price >= book.asks[0].price:
            fill_vol = min(volume, book.asks[0].volume)
            fill_px = book.asks[0].price
            self._positions[instrument_id] = cur + fill_vol
            self._cash -= fill_vol * fill_px
            return MockOrderResponse(success=True, order_id=oid)
        if side == "ask" and book.bids and price <= book.bids[0].price:
            fill_vol = min(volume, book.bids[0].volume)
            fill_px = book.bids[0].price
            self._positions[instrument_id] = cur - fill_vol
            self._cash += fill_vol * fill_px
            return MockOrderResponse(success=True, order_id=oid)

        if order_type == "limit":
            self._outstanding.setdefault(instrument_id, {})[oid] = MockOrderStatus(
                order_id=oid,
                instrument_id=instrument_id,
                price=price,
                volume=volume,
                side=side,
            )
        return MockOrderResponse(success=True, order_id=oid)
