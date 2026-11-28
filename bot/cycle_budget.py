"""Size passive entries from observed flat-to-flat cash flows; no exchange calls."""
from collections import defaultdict, deque
from math import sqrt


class CycleBudget:
    def __init__(self):
        self.state = {}  # inst -> (position, cash in tick-lots, opened lots, known basis)
        self.blocks = defaultdict(lambda: deque(maxlen=15))
        self.tape = defaultdict(lambda: deque(maxlen=128))
        self.possible_ioc_until = {}

    def flag_opening_iocs(self, now, instruments):
        for u in instruments:
            self.possible_ioc_until[u] = now + 5.0

    def _close(self, u, sign, now, cash, lots):
        rows = self.blocks[u, sign]
        minute = int(now // 60)
        if not rows or rows[-1][0] != minute:
            rows.append([minute, 0, 0, 0])
        row = rows[-1]
        row[1] += cash
        row[2] += lots
        row[3] += 1

    def _fills(self, u, now, end, tick, trades):
        fills = []
        for tr in trades:
            side = str(getattr(tr, 'side', '')).lower()
            px = float(getattr(tr, 'price', 0) or 0)
            vol = int(getattr(tr, 'volume', 0) or 0)
            if side not in ('bid', 'ask') or not px > 0 or vol <= 0:
                self.state[u] = (end, 0, 0, end == 0)
                return
            fills.append((vol if side == 'bid' else -vol, round(px / tick)))
        begin = end - sum(q for q, _ in fills)
        pos, cash, lots, known = self.state.get(u, (begin, 0, 0, begin == 0))
        if pos != begin:  # poll and position snapshot disagree: never invent the missing cost basis
            self.state[u] = (end, 0, 0, end == 0)
            return
        for q, px in fills:
            if pos * q < 0:
                sign = 1 if pos > 0 else -1
                closing = -sign * min(abs(pos), abs(q))
                cash -= closing * px
                pos += closing
                q -= closing
                if pos == 0:
                    if known and lots:
                        self._close(u, sign, now, cash, lots)
                    cash, lots, known = 0, 0, True
            if q:
                if now <= self.possible_ioc_until.get(u, float('-inf')):
                    known = False
                cash -= q * px
                lots += abs(q)
                pos += q
        self.state[u] = (pos, cash, lots, known)

    def observe(self, now, instruments, positions, own, public, username=None):
        for u, inst in instruments.items():
            tick = float(getattr(inst, 'tick_size', 0.10) or 0.10)
            self._fills(u, now, int(positions.get(u, 0)), tick, own.get(u, ()))
            polls = self.tape[u]
            while polls and polls[0][0] < now - 5.0:
                polls.popleft()
            at_price = {}
            for tr in public.get(u, ()):
                if username and username in (getattr(tr, 'buyer', None), getattr(tr, 'seller', None)):
                    continue
                side = str(getattr(tr, 'aggressor_side', '')).lower()
                px = float(getattr(tr, 'price', 0) or 0)
                vol = int(getattr(tr, 'volume', 0) or 0)
                if side in ('bid', 'ask') and px > 0 and vol > 0:
                    key = (side, round(px / tick))
                    at_price[key] = at_price.get(key, 0) + vol
            if at_price:
                polls.append((now, at_price))

    def entry(self, now, u, sign, base, can_grow, exit_tick):
        if base <= 0:
            return 0
        minute = int(now // 60)
        rows = [r for r in self.blocks.get((u, sign), ()) if minute - 15 < r[0] <= minute]
        cash = sum(r[1] for r in rows)
        lots = sum(r[2] for r in rows)
        cycles = sum(r[3] for r in rows)
        if cash <= -10 or (cycles >= 4 and lots and cash / lots <= -0.5):
            return 0
        if not can_grow or cash <= 0 or base >= 10:
            return base
        done = [r for r in rows if r[0] < minute]
        n = len(done)
        if n < 4 or sum(r[3] for r in done) < 12:
            return base
        volume = sum(r[2] for r in done)
        mean = sum(r[1] for r in done) / volume
        se = sqrt(n / (n - 1) * sum((r[1] - mean * r[2]) ** 2 for r in done)) / volume
        if mean - 2 * se <= 0:
            return base
        # To exit a long passively we need aggressive buys, and vice versa.
        key = ('bid' if sign > 0 else 'ask', exit_tick)
        prints = [p[key] for ts, p in self.tape.get(u, ()) if now - 5 <= ts <= now and key in p]
        if len(prints) < 2:
            return base
        return max(base, min(10, 2 * base, sum(prints) // 4))

    def apply(self, now, snap, instruments, books, positions):
        for u, plan in snap.quote_plans.items():
            book = books.get(u)
            if not getattr(book, 'bids', None) or not getattr(book, 'asks', None):
                continue
            tick = float(getattr(instruments[u], 'tick_size', 0.10) or 0.10)
            pos = int(positions.get(u, 0))
            grow = pos == 0 and snap.endgame.inv_inc_scale == 1.0
            if pos >= 0:
                plan.bid_lots = self.entry(now, u, 1, plan.bid_lots, grow, round(book.asks[0].price / tick))
            elif plan.bid_lots > 0:
                plan.bid_lots = min(-pos, max(10, plan.bid_lots))
            if pos <= 0:
                plan.ask_lots = self.entry(now, u, -1, plan.ask_lots, grow, round(book.bids[0].price / tick))
            elif plan.ask_lots > 0:
                plan.ask_lots = min(pos, max(10, plan.ask_lots))
