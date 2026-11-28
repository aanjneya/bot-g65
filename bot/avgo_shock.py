"""Directional CRWV maker-entry veto; observes clean AVGO books only."""

from collections import deque
from typing import Optional


class AvgoShockVeto:
    def __init__(self):
        self.samples = deque(maxlen=1024)
        self.regime = 0
        self.side = None
        self.until = float('-inf')
        self.last_time = float('-inf')

    def observe(self, now: float, book) -> Optional[str]:
        if now < self.last_time:
            self.samples.clear()
            self.regime = 0
            self.until = float('-inf')
        self.last_time = now
        bids = getattr(book, 'bids', None) or []
        asks = getattr(book, 'asks', None) or []
        if bids and asks and 0 < float(bids[0].price) < float(asks[0].price):
            mid = (float(bids[0].price) + float(asks[0].price)) / 2
            self.samples.append((now, mid))
            cutoff = now - 5.0
            while len(self.samples) > 1 and self.samples[1][0] <= cutoff:
                self.samples.popleft()
            t0, m0 = self.samples[0]
            sign = 0
            # Use the last observation at or before the horizon, at most 1s stale.
            if cutoff - 1.0 <= t0 <= cutoff:
                move = mid - m0
                sign = 1 if move >= 0.30 - 1e-8 else -1 if move <= -0.30 + 1e-8 else 0
            # One persistent threshold crossing is one event, not a fresh veto every poll.
            if sign and sign != self.regime:
                self.side = 'ask' if sign > 0 else 'bid'
                self.until = now + 10.0
            self.regime = sign
        else:
            self.samples.clear()
            self.regime = 0
        return self.side if now < self.until else None
