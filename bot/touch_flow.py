"""Identify narrow external quotes with recent public trades at both touches."""

from dataclasses import dataclass


@dataclass
class _Touch:
    bid: int
    ask: int
    last_poll: float
    sell_at_bid: float = float('-inf')
    buy_at_ask: float = float('-inf')


class TouchFlow:
    window = 3.0

    def __init__(self):
        self.state = {}

    def observe(self, now, books, public, own, username=None):
        ready = set()
        team = (username or '').strip()
        for u, book in books.items():
            bids = getattr(book, 'bids', None)
            asks = getattr(book, 'asks', None)
            if not bids or not asks:
                self.state.pop(u, None)
                continue
            bp, ap = float(bids[0].price), float(asks[0].price)
            b, a = round(bp * 10), round(ap * 10)
            if b <= 0 or a - b not in (1, 2):
                self.state.pop(u, None)
                continue
            s = self.state.get(u)
            if (s is None or (s.bid, s.ask) != (b, a) or own.get(u)
                    or not 0 <= now - s.last_poll <= self.window):
                # The poll timestamp is shared by the book and the entire print batch.
                # Do not assign transition-batch prints to the new touch.
                self.state[u] = _Touch(b, a, now)
                continue
            s.last_poll = now
            for tr in public.get(u, ()):
                if team and team in (getattr(tr, 'buyer', None), getattr(tr, 'seller', None)):
                    continue
                if int(getattr(tr, 'volume', 0) or 0) <= 0:
                    continue
                px = float(getattr(tr, 'price', 0) or 0)
                side = str(getattr(tr, 'aggressor_side', '')).lower()
                if side == 'ask' and abs(px - bp) < 1e-8:
                    s.sell_at_bid = now
                elif side == 'bid' and abs(px - ap) < 1e-8:
                    s.buy_at_ask = now
            if now - min(s.sell_at_bid, s.buy_at_ask) <= self.window:
                ready.add(u)
        return ready
