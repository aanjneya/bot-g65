"""Short-lived passive entry veto from repricing of both external touches."""

from collections import deque


class MakerPull:
    def __init__(self):
        self.history = {}
        # (instrument, entry side) -> (expiry, reference bid ticks, reference ask ticks)
        self.active = {}

    def observe(self, now, books):
        for key, (until, _, _) in list(self.active.items()):
            if now >= until:
                del self.active[key]

        for u, book in books.items():
            bids = getattr(book, 'bids', None)
            asks = getattr(book, 'asks', None)
            if not bids or not asks:
                self.history.pop(u, None)
                continue
            # Optibook's fixed 0.10 tick. Integer comparisons include exact 3-tick moves.
            b = round(float(bids[0].price) * 10)
            a = round(float(asks[0].price) * 10)
            if not 0 < b < a:
                self.history.pop(u, None)
                continue

            for side in ('ask', 'bid'):
                key = (u, side)
                state = self.active.get(key)
                if state is not None:
                    _, rb, ra = state
                    retraced = (b <= rb and a <= ra) if side == 'ask' else (b >= rb and a >= ra)
                    if retraced:
                        del self.active[key]

            q = self.history.setdefault(u, deque())
            # Don't interpret a data outage as a five-second move.
            if q and now - q[-1][0] > 1.5:
                q.clear()
            if q and q[-1][0] == now:
                q.pop()
            q.append((now, b, a))
            cutoff = now - 5.0
            while len(q) > 1 and q[1][0] <= cutoff:
                q.popleft()
            t, rb, ra = q[0]
            if t > cutoff:
                continue
            if min(b - rb, a - ra) >= 3:
                self.active.setdefault((u, 'ask'), (now + 15.0, rb, ra))
            elif min(rb - b, ra - a) >= 3:
                self.active.setdefault((u, 'bid'), (now + 15.0, rb, ra))

        return set(self.active)
