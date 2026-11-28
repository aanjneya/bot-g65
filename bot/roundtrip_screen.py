"""Causal one-lot shadow maker. No exchange calls and no authority to increase size."""
from __future__ import annotations

from dataclasses import dataclass
from math import sqrt


@dataclass
class Rest:
    side: str
    px: int
    ahead: int


@dataclass
class Ticket:
    bucket: tuple
    entry: int
    sign: int
    born: float
    exit: Rest | None = None


def _queue(levels, px, tick):
    return sum(int(lv.volume) for lv in levels if round(float(lv.price) / tick) == px)


def _hit(order, prints, tick):
    # A print strictly through our price is evidence that even the last lot at
    # our level was reachable. At equality, give earlier displayed size priority.
    for tr in prints:
        ag = str(getattr(tr, 'aggressor_side', '')).lower()
        if ag != ('ask' if order.side == 'bid' else 'bid'):
            continue
        p = float(getattr(tr, 'price', 0) or 0)
        v = int(getattr(tr, 'volume', 0) or 0)
        if p <= 0 or v <= 0:
            continue
        d = (round(p / tick) - order.px) * (1 if order.side == 'bid' else -1)
        if d < 0:
            return True
        if d == 0:
            if v > order.ahead:
                return True
            order.ahead -= v
    return False


class RoundTripScreen:
    def __init__(self):
        self.armed = {}       # entry orders explicitly refreshed in the preceding step
        self.carry = {}       # residual queue, valid only if restaged at same price
        self.holding = {}     # at most one virtual position per instrument/entry side
        self.blocks = {}      # bucket -> minute -> [count, sum of net ticks]
        self.now = 0.0

    def _close(self, key, ticket, px):
        minute = int(self.now // 60)
        rows = self.blocks.setdefault(ticket.bucket, {})
        for old in tuple(rows):
            if old < minute - 20:
                del rows[old]
        row = rows.setdefault(minute, [0, 0.0])
        row[0] += 1
        row[1] += ticket.sign * (px - ticket.entry)
        del self.holding[key]

    def observe(self, now, books, instruments, tape, tail_stop_ticks):
        self.now = now
        self.carry = {}
        for key, (order, bucket) in self.armed.items():
            u, side = key
            tick = float(getattr(instruments[u], 'tick_size', 0.10) or 0.10)
            if _hit(order, tape.get(u, ()), tick):
                self.holding[key] = Ticket(bucket, order.px, 1 if side == 'bid' else -1, now)
            else:
                self.carry[key] = order
        self.armed = {}

        for key, t in list(self.holding.items()):
            u, _ = key
            tick = float(getattr(instruments[u], 'tick_size', 0.10) or 0.10)
            book = books.get(u)
            bids = getattr(book, 'bids', None) or []
            asks = getattr(book, 'asks', None) or []
            expired = now - t.born >= 5.0
            # exit is None on the entry batch. Never reuse prints to close it.
            if not expired and t.exit is not None and _hit(t.exit, tape.get(u, ()), tick):
                self._close(key, t, t.exit.px)
                continue
            if not bids or not asks:
                t.exit = None
                continue  # unresolved inventory is retained, not scored as a win
            bp, ap = round(float(bids[0].price) / tick), round(float(asks[0].price) / tick)
            if bp >= ap:
                t.exit = None
                continue
            liq = bp if t.sign == 1 else ap
            adverse = t.sign * (t.entry - liq)
            if expired or (tail_stop_ticks > 0 and adverse >= tail_stop_ticks):
                self._close(key, t, liq - t.sign)
                continue
            side, px, levels = ('ask', max(bp + 1, ap - 1), asks) if t.sign == 1 else ('bid', min(ap - 1, bp + 1), bids)
            if t.exit is None or t.exit.px != px:
                t.exit = Rest(side, px, _queue(levels, px, tick))

    def permits(self, u, side, price, tick, levels, flat):
        if not levels:
            return False
        key = (u, side)
        px = round(price / tick)
        best = round(float(levels[0].price) / tick)
        distance = (px - best) * (1 if side == 'bid' else -1)
        bucket = (u, side, max(-1, min(1, distance)))
        if flat and key not in self.holding:
            old = self.carry.get(key)
            order = old if old is not None and old.px == px else Rest(side, px, _queue(levels, px, tick))
            self.armed[key] = (order, bucket)
        minute = int(self.now // 60)
        rows = [v for m, v in self.blocks.get(bucket, {}).items() if minute - 20 <= m < minute]
        n = sum(v[0] for v in rows)
        b = len(rows)
        if n < 12 or b < 4:
            return True
        mean = sum(v[1] for v in rows) / n
        # Cluster by minute; veto only when we have statistically negative round-trip evidence.
        se = sqrt(b / (b - 1) * sum((cash - count * mean) ** 2 for count, cash in rows)) / n
        return mean + 1.0 * se > 0.0
