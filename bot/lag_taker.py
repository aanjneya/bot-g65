"""Prospective, size-specific tests of an unchanged follower touch. Prices in ticks."""
from collections import defaultdict, deque
from statistics import mean, stdev

from bot.signals import KNOWN_LEADERS


class LagTaker:
    horizon = 5.0

    def __init__(self, lots):
        self.lots = max(0, int(lots))
        self.history = deque()
        self.waiting = {}
        self.trials = {}
        self.samples = defaultdict(lambda: deque(maxlen=48))

    @staticmethod
    def _book(book):
        bids = getattr(book, 'bids', None) or []
        asks = getattr(book, 'asks', None) or []
        if not bids or not asks:
            return None
        b, a = bids[0], asks[0]
        bp, ap = round(float(b.price) * 10), round(float(a.price) * 10)
        if not 0 < bp < ap or min(b.volume, a.volume) <= 0:
            return None
        return bp, ap, int(b.volume), int(a.volume)

    def _event(self, u, leader, old, cur):
        f, l = cur.get(u), cur.get(leader)
        f0, l0 = old.get(u), old.get(leader)
        if not all((f, l, f0, l0)):
            return None
        leader_return = (l[0] + l[1]) / (l0[0] + l0[1]) - 1.0
        impulse = 0.5 * (f0[0] + f0[1]) * leader_return
        if abs(impulse) < f[1] - f[0] + 1:
            return None
        s = 1 if impulse > 0 else -1
        i = 1 if s == 1 else 0
        if f[i] != f0[i] or f[i + 2] < self.lots:
            return None
        return s, f[i]

    def _score(self, key):
        xs = list(self.samples[key])
        if len(xs) < 24 or any(x is None for x in xs):
            return 0.0
        n = len(xs)
        return min(mean(xs[:n // 2]), mean(xs[n // 2:]), mean(xs) - 2 * stdev(xs) / n ** 0.5)

    def observe(self, now, books, stop_ticks):
        """Return qualified (score, instrument, sign, entry-limit ticks) in priority order."""
        if not self.lots or (self.history and now <= self.history[-1][0]):
            return []
        if self.history and now - self.history[-1][0] > 1.0:
            for u, (s, _, _) in self.trials.items():
                self.samples[u, s].append(None)
            self.waiting.clear()
            self.trials.clear()
            self.history.clear()
        cur = {u: self._book(b) for u, b in books.items()}
        self.history.append((now, cur))
        while len(self.history) > 1 and self.history[1][0] <= now - 1.0:
            self.history.popleft()
        t0, old = self.history[0]
        ready = []
        for u, leader in KNOWN_LEADERS.items():
            if u in self.trials:
                s, entry, due = self.trials[u]
                f = cur.get(u)
                i = 0 if s == 1 else 1
                gross = s * (f[i] - entry) if f else None
                stopped = gross is not None and stop_ticks > 0 and gross <= -stop_ticks
                if f and not stopped and now < due:
                    continue
                net = gross - 1 if f and f[i + 2] >= self.lots else None
                self.samples[u, s].append(net)
                del self.trials[u]
                continue
            queued = self.waiting.pop(u, None)
            if queued is not None:
                ts, anchor, event = queued
                if 0 < now - ts <= 1.0 and self._event(u, leader, anchor, cur) == event:
                    s, entry = event
                    score = self._score((u, s))
                    self.trials[u] = (s, entry, now + self.horizon)
                    if score > 0:
                        ready.append((score, u, s, entry))
                continue
            if 1.0 <= now - t0 <= 1.5:
                event = self._event(u, leader, old, cur)
                if event is not None:
                    self.waiting[u] = (now, old, event)
        return sorted(ready, reverse=True)
