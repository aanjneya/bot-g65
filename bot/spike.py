"""Spike-Catcher: kleine Orders WEIT weg vom Preis. Im Normalbetrieb passiert nichts.
Springt der Preis, werden wir getroffen und wetten darauf, dass er teilweise zurueckspringt.

Nur fuer den Backtester gedacht (noch kein Live-Start):
    python bot/backtest.py data_27_213800 --strategy spike
Vorher pruefen, ob Spruenge zurueckgehen:  python bot/sprung.py data_27_213800
"""
import time

TICK = 0.1
DIST = 8         # Ticks Abstand zur Mitte fuer die Fang-Orders
LOT = 5
RECENTER = 3     # Fang-Orders erst verschieben, wenn die Mitte so viele Ticks gewandert ist
TARGET = 4       # Gewinnziel in Ticks (Rueckkehr) nach einem Fill
STOP = 8         # Verlust-Stopp in Ticks, falls der Preis weiterlaeuft
HOLD = 30        # Sekunden: spaetestens dann glattstellen
EPS = 1e-9


def r1(x):
    return round(x + 0.0, 1)


class SpikeCatcher:
    def __init__(self, ex, folder=None):
        self.ex = ex
        self.inst = list(getattr(ex, "instruments", [])) or \
            sorted(ex.get_tradable_instruments().keys())
        self.center = {}
        self.entry = {}     # inst -> (preis, zeit)

    def _book(self, i):
        b = self.ex.get_last_price_book(i)
        mine = self.ex.get_outstanding_orders(i)
        own = {}
        for o in mine.values():
            own[(o.side, r1(o.price))] = own.get((o.side, r1(o.price)), 0) + o.volume
        bids = [(r1(l.price), l.volume - own.get(("bid", r1(l.price)), 0)) for l in b.bids]
        asks = [(r1(l.price), l.volume - own.get(("ask", r1(l.price)), 0)) for l in b.asks]
        return [x for x in bids if x[1] > 0], [x for x in asks if x[1] > 0], mine

    def _flatten(self, i, pos, bids, asks):
        self.ex.delete_orders(i)
        if pos > 0 and bids:
            self.ex.insert_order(i, price=bids[0][0], volume=pos, side="ask", order_type="ioc")
        elif pos < 0 and asks:
            self.ex.insert_order(i, price=asks[0][0], volume=-pos, side="bid", order_type="ioc")

    def step(self):
        now = time.time()
        positions = self.ex.get_positions()
        for i in self.inst:
            self.ex.poll_new_trades(i)
            bids, asks, mine = self._book(i)
            if not bids or not asks:
                continue
            mid = (bids[0][0] + asks[0][0]) / 2
            pos = positions.get(i, 0)

            if pos != 0:
                if i not in self.entry:
                    fills = [o for o in self.ex.get_trade_history(i)] if hasattr(self.ex, "get_trade_history") else []
                    px = fills[-1].price if fills else mid
                    self.entry[i] = (px, now)
                px, t0 = self.entry[i]
                against = (px - mid) if pos > 0 else (mid - px)
                if against >= STOP * TICK - EPS or now - t0 > HOLD:
                    self._flatten(i, pos, bids, asks)
                    continue
                # Ziel: Teil-Rueckkehr passiv mitnehmen
                side = "ask" if pos > 0 else "bid"
                target = r1(px + TARGET * TICK) if pos > 0 else r1(px - TARGET * TICK)
                if side == "ask":
                    target = max(target, r1(bids[0][0] + TICK))
                else:
                    target = min(target, r1(asks[0][0] - TICK))
                have = [(o.side, r1(o.price), o.volume) for o in mine.values()]
                if have != [(side, target, abs(pos))]:
                    if mine:
                        self.ex.delete_orders(i)
                    self.ex.insert_order(i, price=target, volume=abs(pos), side=side, order_type="limit")
                continue

            self.entry.pop(i, None)
            c = self.center.get(i)
            if c is not None and abs(mid - c) < RECENTER * TICK - EPS and len(mine) == 2:
                continue
            self.center[i] = mid
            bid_p = r1(mid - DIST * TICK)
            ask_p = r1(mid + DIST * TICK)
            if mine:
                self.ex.delete_orders(i)
            if bid_p < asks[0][0] - EPS:
                self.ex.insert_order(i, price=bid_p, volume=LOT, side="bid", order_type="limit")
            if ask_p > bids[0][0] + EPS:
                self.ex.insert_order(i, price=ask_p, volume=LOT, side="ask", order_type="limit")
