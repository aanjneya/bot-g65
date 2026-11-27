"""Backtester: spielt aufgezeichnete Marktdaten ab und laesst eine Strategie dagegen handeln.
Die Strategie bekommt eine simulierte Boerse mit derselben API wie Optibook, also laeuft
derselbe Code wie live (z. B. der Wand-Bot) ohne Aenderung.

Start:
    python bot/backtest.py data_27_213800                    # Strategie: simple (Vergleich)
    python bot/backtest.py data_27_213800 --strategy wall    # Wand-Bot
    python bot/backtest.py data_hist_xyz --strategy wall      # historische Daten (hist_export.py)
Schreibt <datenordner>/backtest_<strategie>.md und backtest_<strategie>_pnl.csv.

Wie realistisch?
- Aggressive Orders (IOC / kreuzende Limits) laufen gegen das Buch zum Zeitpunkt der Order.
- Passive Orders stehen hinten in der Warteschlange ihres Preises. Sie werden gefuellt,
  wenn oeffentliche Trades die Menge vor uns aufgebraucht haben oder durch unseren Preis laufen.
- Latenz: neue Orders sind erst nach LATENCY Sekunden aktiv.
- Grenzen: Unsere Orders bewegen den abgespielten Markt NICHT (keine Reaktion anderer Bots).
  Ergebnisse sind daher eher zu optimistisch fuer grosse Mengen.
- Positionslimit 100 (inkl. offener Orders), Self-Trade und > 25 Anfragen/s beenden den Lauf
  wie auf Optibook (Verbindung getrennt).
"""
import argparse
import csv
import os
import sys
from bisect import bisect_left
from collections import deque
from types import SimpleNamespace as NS

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wand import load  # noqa: E402  (gleiches Datenformat wie collector.py)

TICK = 0.1
LATENCY = 0.05      # Sekunden bis eine neue Order im Buch steht
STEP = 0.2          # Sekunden zwischen zwei Strategie-Durchlaeufen
POS_LIMIT = 100
MAX_REQ = 25        # Optibook trennt ab hier
EPS = 1e-9
MARKOUTS = [1, 5]


class Disconnected(BaseException):
    """BaseException, damit ein 'except Exception' in der Strategie den Abbruch nicht schluckt."""


class SimClock:
    """Ersetzt das time-Modul der Strategie: Zeit laeuft nur, wenn der Backtester es sagt."""

    def __init__(self, t):
        self.t = t

    def time(self):
        return self.t

    def monotonic(self):
        return self.t

    def sleep(self, dt):
        self.t += max(dt, 0.0)


class SimExchange:
    """Boerse mit der Optibook-API (die Teile, die Bots nutzen)."""

    def __init__(self, instruments, clock):
        self.clock = clock
        self.instruments = instruments
        self.book = {i: ([], []) for i in instruments}       # Buch der anderen
        self.last_trade = {i: None for i in instruments}
        self.orders = {}                                     # id -> Order
        self.next_id = 1
        self.pos = {i: 0 for i in instruments}
        self.cash = {i: 0.0 for i in instruments}
        self.new_fills = {i: [] for i in instruments}
        self.fills = []                                      # alle eigenen Fills
        self.req_times = deque()
        self.connected = True
        self.errors = []
        self.max_req_sec = 0

    # ---------- Hilfen ----------
    def _request(self):
        now = self.clock.time()
        self.req_times.append(now)
        while self.req_times and now - self.req_times[0] >= 1.0:
            self.req_times.popleft()
        self.max_req_sec = max(self.max_req_sec, len(self.req_times))
        if len(self.req_times) > MAX_REQ:
            self._disconnect(f"mehr als {MAX_REQ} Anfragen pro Sekunde")

    def _disconnect(self, why):
        self.connected = False
        self.errors.append(why)
        raise Disconnected(why)

    def _fill(self, inst, side, price, vol, kind):
        s = 1 if side == "bid" else -1
        self.pos[inst] += s * vol
        self.cash[inst] -= s * vol * price
        f = NS(timestamp=self.clock.time(), instrument_id=inst, side=side, price=price,
               volume=vol, kind=kind, order_id=None, trade_id=None)
        self.new_fills[inst].append(f)
        self.fills.append(f)

    def mid(self, inst):
        b, a = self.book[inst]
        if b and a:
            return (b[0][0] + a[0][0]) / 2
        return self.last_trade[inst]

    # ---------- Optibook-API ----------
    def is_connected(self):
        return self.connected

    def connect(self):
        pass

    def disconnect(self):
        self.connected = False

    def get_instruments(self):
        return {i: NS(instrument_id=i, tick_size=TICK, price_change_limit=None, price_limit=None)
                for i in self.instruments}

    get_tradable_instruments = get_instruments

    def get_last_price_book(self, inst):
        bids, asks = (dict(x) for x in self.book[inst])
        for o in self.orders.values():
            if o.instrument_id == inst:
                d = bids if o.side == "bid" else asks
                d[o.price] = d.get(o.price, 0) + o.volume
        return NS(instrument_id=inst, timestamp=self.clock.time(),
                  bids=[NS(price=p, volume=v) for p, v in sorted(bids.items(), reverse=True)],
                  asks=[NS(price=p, volume=v) for p, v in sorted(asks.items())])

    def get_outstanding_orders(self, inst):
        return {k: NS(order_id=k, instrument_id=inst, price=o.price, volume=o.volume, side=o.side)
                for k, o in self.orders.items() if o.instrument_id == inst}

    def get_positions(self):
        return dict(self.pos)

    def get_positions_and_cash(self):
        return {i: {"volume": self.pos[i], "cash": self.cash[i]} for i in self.instruments}

    def poll_new_trades(self, inst):
        out, self.new_fills[inst] = self.new_fills[inst], []
        return out

    def get_trade_history(self, inst):
        return [f for f in self.fills if f.instrument_id == inst]

    def poll_new_trade_ticks(self, inst):
        return []

    def poll_new_social_media_feeds(self):
        return []

    def get_pnl(self, valuations=None):
        total = 0.0
        for i in self.instruments:
            px = (valuations or {}).get(i, self.last_trade[i] or self.mid(i) or 0.0)
            total += self.cash[i] + self.pos[i] * px
        return total

    def delete_order(self, inst, *, order_id):
        self._request()
        ok = self.orders.pop(order_id, None) is not None
        return NS(success=ok, error_reason=None if ok else "unbekannte Order")

    def delete_orders(self, inst):
        self._request()
        for k in [k for k, o in self.orders.items() if o.instrument_id == inst]:
            del self.orders[k]

    def amend_order(self, inst, *, order_id, volume):
        self._request()
        o = self.orders.get(order_id)
        if o is None or volume > o.volume or volume <= 0:
            return NS(success=False, error_reason="amend nicht moeglich")
        o.volume = volume
        return NS(success=True, error_reason=None)

    def insert_order(self, inst, *, price, volume, side, order_type="limit"):
        self._request()
        price = round(price, 1)
        if volume <= 0 or side not in ("bid", "ask"):
            return NS(success=False, order_id=None, error_reason="ungueltig")
        # Positionslimit inkl. offener Orders (wie Optibook)
        open_vol = sum(o.volume for o in self.orders.values()
                       if o.instrument_id == inst and o.side == side)
        s = 1 if side == "bid" else -1
        if abs(self.pos[inst] + s * (open_vol + volume)) > POS_LIMIT:
            return NS(success=False, order_id=None, error_reason="Positionslimit")
        # Self-Trade: kreuzt eine eigene Order der Gegenseite
        for o in self.orders.values():
            if o.instrument_id == inst and o.side != side and \
                    ((side == "bid" and price >= o.price - EPS) or (side == "ask" and price <= o.price + EPS)):
                self._disconnect("Self-Trade (Order wuerde gegen eigene Order laufen)")
        # Aggressiver Teil gegen das Buch der anderen
        bids, asks = self.book[inst]
        levels = asks if side == "bid" else bids
        left = volume
        new_levels = []
        for p, v in levels:
            crosses = (side == "bid" and p <= price + EPS) or (side == "ask" and p >= price - EPS)
            if crosses and left > 0:
                q = min(v, left)
                self._fill(inst, side, p, q, "aggressiv")
                left -= q
                v -= q
            if v > 0:
                new_levels.append((p, v))
        # Was wir genommen haben, ist bis zum naechsten Snapshot weg
        if side == "bid":
            self.book[inst] = (bids, new_levels)
        else:
            self.book[inst] = (new_levels, asks)
        if order_type == "ioc" or left == 0:
            return NS(success=True, order_id=None, error_reason=None)
        own = bids if side == "bid" else asks
        queue = sum(v for p, v in own if abs(p - price) < EPS)
        oid = self.next_id
        self.next_id += 1
        self.orders[oid] = NS(instrument_id=inst, side=side, price=price, volume=left,
                              queue=queue, active_at=self.clock.time() + LATENCY)
        return NS(success=True, order_id=oid, error_reason=None)

    # ---------- Markt-Ereignisse ----------
    def on_book(self, inst, bids, asks):
        self.book[inst] = (bids, asks)
        now = self.clock.time()
        for k, o in list(self.orders.items()):
            if o.instrument_id != inst or o.active_at > now:
                continue
            same = bids if o.side == "bid" else asks
            level = sum(v for p, v in same if abs(p - o.price) < EPS)
            o.queue = min(o.queue, level)          # Stornos vor uns rutschen uns nach vorn
            # Andere stellen kreuzend ein -> sie haetten mit uns gehandelt
            opp = asks if o.side == "bid" else bids
            for p, v in opp:
                if (o.side == "bid" and p <= o.price + EPS) or (o.side == "ask" and p >= o.price - EPS):
                    q = min(v, o.volume)
                    self._fill(inst, o.side, o.price, q, "passiv")
                    o.volume -= q
                    if o.volume == 0:
                        del self.orders[k]
                        break
                else:
                    break

    def on_trade(self, inst, price, vol):
        self.last_trade[inst] = price
        now = self.clock.time()
        # Kaeufe der anderen treffen unsere Asks, Verkaeufe unsere Bids
        mine = [(k, o) for k, o in self.orders.items()
                if o.instrument_id == inst and o.active_at <= now]
        mine.sort(key=lambda x: (-x[1].price if x[1].side == "bid" else x[1].price))
        for k, o in mine:
            through = (o.side == "bid" and price < o.price - EPS) or \
                      (o.side == "ask" and price > o.price + EPS)
            at = abs(price - o.price) < EPS
            if not (through or at):
                continue
            avail = vol
            if at:
                take = min(o.queue, avail)
                o.queue -= take
                avail -= take
            q = min(avail, o.volume)
            if q > 0:
                self._fill(inst, o.side, o.price, q, "passiv")
                o.volume -= q
                if o.volume == 0:
                    del self.orders[k]


# ---------- Strategien ----------

class SimpleMaker:
    """Vergleichs-Strategie: am besten Preis mitstellen, nach Position verschieben."""
    LOT, MAX_POS = 5, 30

    def __init__(self, ex, folder):
        self.ex = ex
        self.inst = list(ex.instruments)

    def step(self):
        pos = self.ex.get_positions()
        for i in self.inst:
            mine = self.ex.get_outstanding_orders(i)
            b = self.ex.book[i]
            if not b[0] or not b[1]:
                continue
            bb, ba = b[0][0][0], b[1][0][0]
            p = pos[i]
            skew = -round(p / self.MAX_POS * 2) * TICK
            want = {"bid": (round(bb + skew, 1), self.LOT) if p < self.MAX_POS else None,
                    "ask": (round(ba + skew, 1), self.LOT) if p > -self.MAX_POS else None}
            if want["bid"] and want["bid"][0] >= ba:
                want["bid"] = None
            if want["ask"] and want["ask"][0] <= bb:
                want["ask"] = None
            if want["bid"] and want["ask"] and want["bid"][0] >= want["ask"][0]:
                want["bid"] = None
            have = {(o.side, o.price, o.volume) for o in mine.values()}
            target = {(s, *w) for s, w in want.items() if w}
            if have != target:
                if mine:
                    self.ex.delete_orders(i)
                for s, pr, v in target:
                    self.ex.insert_order(i, price=pr, volume=v, side=s, order_type="limit")


def make_strategy(name, ex, clock, folder):
    if name == "simple":
        return SimpleMaker(ex, folder)
    if name == "wall":
        import wall_maker
        wall_maker.time = clock               # Strategie sieht die Simulationszeit
        wall_maker.INSTRUMENTS[:] = list(ex.instruments)
        return wall_maker.WallMaker(ex, folder)
    if ":" in name:                           # eigene Strategie: modul:Klasse(ex, folder)
        mod, cls = name.split(":")
        m = __import__(mod)
        if hasattr(m, "time"):
            m.time = clock
        return getattr(m, cls)(ex, folder)
    raise SystemExit(f"Unbekannte Strategie: {name}")


# ---------- Ablauf ----------

def run(folder, strategy, start_frac=0.0, end_frac=1.0):
    books, trades = load(folder)
    events = []
    for inst, snaps in books.items():
        for t, b, a in snaps:
            events.append((t, 0, inst, b, a))
    for inst, ts in trades.items():
        for t, p, v in ts:
            events.append((t, 1, inst, p, v))
    events.sort(key=lambda e: (e[0], e[1]))
    if not events:
        raise SystemExit("Keine Daten gefunden.")
    t0_all, t1_all = events[0][0], events[-1][0]
    span = t1_all - t0_all
    t_start = t0_all + span * start_frac
    t_end = t0_all + span * end_frac

    clock = SimClock(t0_all)
    ex = SimExchange(sorted(books), clock)
    out_dir = os.path.join(folder, f"backtest_{strategy.replace(':', '_')}_log")
    os.makedirs(out_dir, exist_ok=True)
    strat = make_strategy(strategy, ex, clock, out_dir)

    curve = []
    k = 0
    next_step = t_start
    stopped = None
    pending, seen = [], 0
    try:
        while k < len(events) or clock.t < t_end:
            t_ev = events[k][0] if k < len(events) else float("inf")
            if t_ev <= next_step:
                clock.t = max(clock.t, t_ev)
                _, kind, inst, x, y = events[k]
                if kind == 0:
                    ex.on_book(inst, x, y)
                else:
                    ex.on_trade(inst, x, y)
                k += 1
                continue
            if next_step > t_end:
                break
            clock.t = max(clock.t, next_step)
            if clock.t >= t_start:
                strat.step()
                curve.append((clock.t - t_start, ex.get_pnl(valuations=mids(ex)),
                              sum(abs(v) for v in ex.pos.values())))
            next_step = max(next_step + STEP, clock.t)
            # Markouts nachtragen
            while seen < len(ex.fills):
                ex.fills[seen].marks = {}
                pending.append(ex.fills[seen])
                seen += 1
            still = []
            for f in pending:
                for h in MARKOUTS:
                    if h not in f.marks and clock.t - f.timestamp >= h:
                        m = ex.mid(f.instrument_id)
                        if m is not None:
                            s = 1 if f.side == "bid" else -1
                            f.marks[h] = s * (m - f.price)
                if len(f.marks) < len(MARKOUTS):
                    still.append(f)
            pending = still
    except Disconnected as exc:
        stopped = str(exc)
    return ex, curve, stopped, (t_start, t_end), out_dir


def mids(ex):
    return {i: ex.mid(i) for i in ex.instruments if ex.mid(i) is not None}


def report(folder, strategy, ex, curve, stopped, span):
    name = strategy.replace(":", "_")
    final = curve[-1][1] if curve else 0.0
    peak, dd = 0.0, 0.0
    for _, p, _ in curve:
        peak = max(peak, p)
        dd = min(dd, p - peak)
    half_t = (span[1] - span[0]) / 2
    first = [p for t, p, _ in curve if t <= half_t]
    h1 = first[-1] if first else 0.0
    h2 = final - h1
    max_gross = max((g for _, _, g in curve), default=0)
    lines = [f"# Backtest: {strategy} auf {folder}", "",
             f"Dauer: {(span[1] - span[0]) / 60:.1f} min. PnL bewertet zur Mitte des Buchs.", ""]
    if stopped:
        lines += [f"**ABBRUCH wie auf Optibook: {stopped}**", ""]
    lines += ["| Kennzahl | Wert |", "|---|---|",
              f"| PnL gesamt | {final:+.1f} |",
              f"| 1. Haelfte / 2. Haelfte | {h1:+.1f} / {h2:+.1f} |",
              f"| Groesster Rueckgang | {dd:.1f} |",
              f"| Fills (passiv / aggressiv) | {sum(f.kind == 'passiv' for f in ex.fills)} / "
              f"{sum(f.kind == 'aggressiv' for f in ex.fills)} |",
              f"| Max. Summe aller Positionen | {max_gross} |",
              f"| Max. Anfragen pro Sekunde | {ex.max_req_sec} |", "",
              "## Pro Aktie", "",
              "| Aktie | PnL | Fills | Endposition | Markout 1s | Markout 5s |", "|---|---|---|---|---|---|"]
    m = mids(ex)
    for i in ex.instruments:
        fs = [f for f in ex.fills if f.instrument_id == i]
        pnl = ex.cash[i] + ex.pos[i] * m.get(i, 0.0)
        mk = []
        for h in MARKOUTS:
            vals = [f.marks[h] for f in fs if getattr(f, "marks", {}).get(h) is not None]
            mk.append(f"{sum(vals) / len(vals):+.3f}" if vals else "-")
        lines.append(f"| {i} | {pnl:+.1f} | {len(fs)} | {ex.pos[i]} | {mk[0]} | {mk[1]} |")
    lines += ["", "Markout = Preisbewegung nach unserem Fill zu unseren Gunsten (positiv = gut).",
              "Negativer Markout = Adverse Selection: wir werden vor allem dann gefuellt, wenn der",
              "Preis danach gegen uns laeuft.",
              "Grenzen: unsere Orders bewegen den abgespielten Markt nicht; IOCs treffen das Buch",
              "ohne Verzoegerung. Fuer kleine Mengen ist das brauchbar, fuer grosse zu optimistisch."]
    text = "\n".join(lines) + "\n"
    with open(os.path.join(folder, f"backtest_{name}.md"), "w", encoding="utf-8") as f:
        f.write(text)
    with open(os.path.join(folder, f"backtest_{name}_pnl.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["sekunde", "pnl", "summe_positionen"])
        for row in curve[::5]:
            w.writerow([f"{row[0]:.1f}", f"{row[1]:.2f}", row[2]])
    return text


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder", help="Datenordner mit books.csv und trades.csv")
    ap.add_argument("--strategy", default="simple",
                    help="simple | wall | modul:Klasse (Klasse(ex, ordner) mit .step())")
    ap.add_argument("--start", type=float, default=0.0, help="Anteil 0..1, ab wann handeln")
    ap.add_argument("--end", type=float, default=1.0, help="Anteil 0..1, bis wann")
    args = ap.parse_args()
    ex, curve, stopped, span, _ = run(args.folder, args.strategy, args.start, args.end)
    print(report(args.folder, args.strategy, ex, curve, stopped, span))


if __name__ == "__main__":
    main()
