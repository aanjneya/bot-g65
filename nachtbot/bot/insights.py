"""Auswertung fuer collector.py. Nur Standardbibliothek.
Zeit t = Sekunden (float). Ausgewertet wird die letzte Stunde.
"""
from collections import defaultdict
from datetime import datetime
from math import sqrt

WINDOW = 3600      # ausgewertete Sekunden
HORIZON = 5        # "was passiert 5 Sekunden spaeter?"
MAX_LAG = 5        # Lead-Lag bis 5 s
WALL = 400         # ab dieser Menge gilt eine Order als "gross"
MIN_N = 200        # darunter: zu wenig Daten


def corr(x, y):
    pairs = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]
    n = len(pairs)
    if n < MIN_N:
        return None, n
    mx = sum(a for a, _ in pairs) / n
    my = sum(b for _, b in pairs) / n
    sxy = sum((a - mx) * (b - my) for a, b in pairs)
    sxx = sum((a - mx) ** 2 for a, _ in pairs)
    syy = sum((b - my) ** 2 for _, b in pairs)
    if sxx <= 0 or syy <= 0:
        return None, n
    return sxy / sqrt(sxx * syy), n


class Analyzer:
    def __init__(self, instruments):
        self.instruments = instruments
        self.start = None
        self.current = {}                                   # letzter Stand je Aktie
        self.grid = {i: {} for i in instruments}            # Sekunde -> (mid, spread, imb, wallgap)
        self.last_sec = None
        self.trades = []                                    # (sek, aktie, preis, menge, seite, kaeufer, verkaeufer)
        self.news = []                                      # (sek, text)

    def sec(self, t):
        if self.start is None:
            self.start = t
        return int(t - self.start)

    def add_book(self, inst, bids, asks):
        """bids/asks: Listen von (preis, menge), beste zuerst."""
        if not bids or not asks:
            self.current.pop(inst, None)
            return
        (bp, bv), (ap, av) = bids[0], asks[0]
        mid = (bp + ap) / 2
        big_bid = next((p for p, v in bids if v >= WALL), None)
        big_ask = next((p for p, v in asks if v >= WALL), None)
        wallgap = None
        if big_bid is not None and big_ask is not None:
            wallgap = (big_bid + big_ask) / 2 - mid
        self.current[inst] = (mid, ap - bp, (bv - av) / (bv + av), wallgap)

    def tick(self, t):
        """Einmal pro Durchlauf: Stand ins 1-Sekunden-Raster schreiben."""
        s = self.sec(t)
        if s == self.last_sec:
            return
        self.last_sec = s
        for inst, state in self.current.items():
            self.grid[inst][s] = state

    def add_trade(self, t, inst, price, volume, side, buyer, seller):
        self.trades.append((self.sec(t), inst, price, volume, side, buyer, seller))

    def add_news(self, t, text):
        self.news.append((self.sec(t), text))

    # ---------- Auswertungen ----------
    def mid(self, inst, s):
        row = self.grid[inst].get(s)
        return row[0] if row else None

    def move(self, inst, s, h):
        a, b = self.mid(inst, s), self.mid(inst, s + h)
        return None if a is None or b is None else b - a

    def report(self):
        end = self.last_sec or 0
        secs = range(max(0, end - WINDOW), end + 1)
        out = [f"# Erkenntnisse (Stand {datetime.now():%d.%m. %H:%M:%S})",
               f"Datenmenge: {len(secs)} Sekunden. Alles sind Hinweise, keine Beweise.", ""]

        out += ["## 1. Aktien", "| Aktie | Spread | Bewegung in 5s (typisch) |", "|---|---|---|"]
        for i in self.instruments:
            rows = [self.grid[i][s] for s in secs if s in self.grid[i]]
            moves = [m for m in (self.move(i, s, HORIZON) for s in secs) if m is not None]
            if rows and moves:
                spread = sum(r[1] for r in rows) / len(rows)
                typ = sqrt(sum(m * m for m in moves) / len(moves))
                out.append(f"| {i} | {spread:.2f} | {typ:.2f} |")

        out += ["", "## 2. Wer handelt aktiv und verdient er dabei?",
                "Ergebnis < 0: der Aktive verliert -> Market Making gegen ihn lohnt.",
                "Ergebnis > 0: er weiss mehr -> nicht dagegenhalten.", "",
                "| Name | Trades | davon Kaeufe | Menge im Schnitt | Ergebnis je Stueck nach 5s |",
                "|---|---|---|---|---|"]
        stats = defaultdict(lambda: [0, 0, 0, 0.0, 0])     # n, kaeufe, menge, ergebnis, menge_bewertet
        for s, inst, price, vol, side, buyer, seller in self.trades:
            if s < secs.start:
                continue
            buy = side == "bid"
            st = stats[buyer if buy else seller]
            st[0] += 1
            st[1] += buy
            st[2] += vol
            later = self.mid(inst, s + HORIZON)
            if later is not None:
                st[3] += (1 if buy else -1) * (later - price) * vol
                st[4] += vol
        for name, (n, buys, vol, pnl, vv) in sorted(stats.items(), key=lambda kv: -kv[1][0])[:15]:
            res = f"{pnl / vv:+.3f}" if vv and n >= 30 else "zu wenig Daten"
            out.append(f"| {name or '?'} | {n} | {buys / n:.0%} | {vol / n:.1f} | {res} |")

        out += ["", "## 3. Sagt das Orderbuch die naechsten 5s voraus?",
                "Zahl von -1 bis +1. Ab etwa 0.10 interessant.",
                "\"kein Wert\" = zu wenig Daten oder Signal aendert sich nie.", "",
                "| Aktie | Ungleichgewicht | Mitte der grossen Orders |", "|---|---|---|"]
        for i in self.instruments:
            future = [self.move(i, s, HORIZON) for s in secs]
            cells = []
            for col in (2, 3):
                feature = [self.grid[i][s][col] if s in self.grid[i] else None for s in secs]
                c, n = corr(feature, future)
                cells.append("kein Wert" if c is None else f"{c:+.2f}")
            out.append(f"| {i} | {cells[0]} | {cells[1]} |")

        out += ["", "## 4. Lead-Lag (wer bewegt sich zuerst?)"]
        returns = {i: [self.move(i, s, 1) for s in secs] for i in self.instruments}
        found = []
        for a in self.instruments:
            for b in self.instruments:
                if a == b:
                    continue
                for lag in range(1, MAX_LAG + 1):
                    c, n = corr(returns[a][:-lag], returns[b][lag:])
                    if c is not None and abs(c) >= max(0.10, 4 / sqrt(n)):
                        found.append((abs(c), c, a, b, lag))
        for _, c, a, b, lag in sorted(found, reverse=True)[:8]:
            out.append(f"- {a} bewegt sich, {b} folgt {lag}s spaeter ({c:+.2f})")
        if not found:
            out.append("- Noch nichts Auffaelliges.")

        out += ["", "## 5. Grosse Spruenge (letzte 10)"]
        jumps = []
        for i in self.instruments:
            moves = [(s, self.move(i, s, 2)) for s in secs]
            moves = [(s, m) for s, m in moves if m is not None]
            if len(moves) < MIN_N:
                continue
            limit = max(1.0, 6 * sqrt(sum(m * m for _, m in moves) / len(moves)))
            last = -99
            for s, m in moves:
                if abs(m) >= limit and s - last > 10:
                    near = [text for ns, text in self.news if -10 <= s - ns <= 10]
                    jumps.append((s, i, m, near))
                    last = s
        for s, i, m, near in sorted(jumps)[-10:]:
            when = datetime.fromtimestamp(self.start + s).strftime("%H:%M:%S")
            out.append(f"- {when} {i} {m:+.2f}" + (f" | News: {near[0]}" if near else " | keine News"))
        if not jumps:
            out.append("- Keine.")

        out += ["", f"## 6. News ({len(self.news)} insgesamt, letzte 10)"]
        for s, text in self.news[-10:]:
            moved = [(abs(m), i, m) for i in self.instruments
                     if (m := self.move(i, s, 15)) is not None]
            big = max(moved) if moved else None
            out.append(f"- {text}" + (f" -> staerkste Bewegung 15s: {big[1]} {big[2]:+.2f}" if big else ""))
        return "\n".join(out) + "\n"
