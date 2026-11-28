"""Causal public-tape evidence for sizing passive entries, never exits."""

from collections import defaultdict, deque
from math import sqrt


class PassiveEntryEvidence:
    def __init__(self):
        self.asof = None
        self.quotes = {}     # quotes proposed before the next trade batch
        self.pending = {}    # at most one outstanding mark per instrument/side
        self.history = defaultdict(lambda: deque(maxlen=32))

    def observe(self, now, trade_ticks, mids, own_username=None):
        previous, self.quotes = self.quotes, {}
        last, self.asof = self.asof, now
        for key, (due, price, tick, sign) in list(self.pending.items()):
            if now < due:
                continue
            del self.pending[key]
            mid = mids.get(key[0])
            if mid is not None and now - due <= 2.0:
                self.history[key].append((now, sign * (mid - price) / tick))

        # Do not attribute a reconnect gap's prints to a stale paper order.
        if last is None or not 0.0 < now - last <= 2.0:
            return
        for key, (price, tick, ahead) in previous.items():
            if key in self.pending:
                continue
            inst, side = key
            sign = 1 if side == 'bid' else -1
            opposite = 'ask' if sign == 1 else 'bid'
            at_price = 0
            for tr in trade_ticks.get(inst, ()):
                if str(getattr(tr, 'aggressor_side', '')).lower() != opposite:
                    continue
                who = getattr(tr, 'seller' if sign == 1 else 'buyer', None)
                if own_username and who == own_username:
                    continue
                px = float(getattr(tr, 'price', 0) or 0)
                vol = int(getattr(tr, 'volume', 0) or 0)
                if px <= 0 or vol <= 0:
                    continue
                diff = sign * (px - price)
                if abs(diff) < tick * 0.5:
                    at_price += vol
                if diff < -tick * 0.5 or at_price > ahead:
                    self.pending[key] = (now + 5.0, price, tick, sign)
                    break

    def entry_lots(self, inst, side, price, tick, requested, held, levels):
        if requested <= 0:
            return 0
        key = (inst, side)
        ahead = sum(int(lv.volume) for lv in levels if abs(float(lv.price) - price) < tick * 0.5)
        # Arm even if the sizing decision below is zero. No exchange request.
        self.quotes[key] = (price, tick, ahead)
        hist = self.history[key]
        while hist and self.asof is not None and self.asof - hist[0][0] > 300.0:
            hist.popleft()
        n = len(hist)
        if n >= 6:
            mean = sum(x for _, x in hist) / n
            if mean <= 0.0:
                return 0
            se = sqrt(sum((x - mean) ** 2 for _, x in hist) / (n * (n - 1)))
            if mean > 2.0 * se:
                return requested
        return min(requested, max(0, 1 - held))
