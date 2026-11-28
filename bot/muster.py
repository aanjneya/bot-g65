"""Musterfinder. Liest die Daten von collector.py und misst die Auswirkung.
Start:  python muster.py            (nimmt den neuesten data_ Ordner)
        python muster.py data_xyz
Ergebnis: muster.md und muster.csv im Datenordner. Sendet KEINE Orders.

Ehrlichkeitsregeln:
- Einstieg erst 1 Sekunde NACH dem Signal (so schnell muss der Bot erst mal sein).
- Richtung wird in der 1. Haelfte der Daten bestimmt, in der 2. Haelfte geprueft.
- Kosten = Spread (kaufen zum Ask, verkaufen zum Bid).
"""
import csv
import glob
import os
import sys
from collections import defaultdict
from datetime import datetime
from math import sqrt

HORIZONS = [1, 5, 15, 60]
DELAY = 1
MIN_EVENTS = 30      # je Haelfte
MIN_T = 2.0          # wie deutlich der Effekt sein muss
WALL = 400


def read_rows(path):
    if not os.path.exists(path):
        return
    with open(path, newline="", encoding="utf-8") as f:
        yield from csv.DictReader(f)


def parse_levels(text):
    return [(float(p), int(v)) for p, v in (x.split(":") for x in text.split("|") if x)]


def load(folder):
    books = defaultdict(dict)   # aktie -> sekunde -> (mid, spread, ungleichgewicht, wandabstand)
    start = None
    end = 0
    for row in read_rows(os.path.join(folder, "books.csv")):
        try:
            t = datetime.fromisoformat(row["time"]).timestamp()
            bids, asks = parse_levels(row["bids"]), parse_levels(row["asks"])
        except (ValueError, TypeError, KeyError, AttributeError):
            continue            # halb geschriebene Zeile
        start = t if start is None else start
        s = int(t - start)
        end = max(end, s)
        if not bids or not asks:
            books[row["instrument"]][s] = None
            continue
        (bp, bv), (ap, av) = bids[0], asks[0]
        mid = (bp + ap) / 2
        wb = next((p for p, v in bids if v >= WALL), None)
        wa = next((p for p, v in asks if v >= WALL), None)
        gap = (wb + wa) / 2 - mid if wb is not None and wa is not None else None
        books[row["instrument"]][s] = (mid, ap - bp, (bv - av) / (bv + av), gap)

    # gespeichert wird nur bei Aenderung -> Luecken auffuellen
    grid = {}
    for inst, changes in books.items():
        series, last = [], None
        for s in range(end + 1):
            last = changes.get(s, last)
            series.append(last)
        grid[inst] = series

    trades = []
    for row in read_rows(os.path.join(folder, "trades.csv")):
        try:
            s = int(datetime.fromisoformat(row["time"]).timestamp() - start)
            vol = int(float(row["volume"]))
        except (ValueError, TypeError, KeyError):
            continue
        if 0 <= s <= end:
            trades.append((s, row["instrument"], vol, row["aggressor_side"], row["buyer"], row["seller"]))
    return grid, trades, end


def find_events(grid, trades):
    """muster -> Liste (sekunde, zielaktie, richtung). richtung +1/-1 = Richtung des Signals."""
    events = defaultdict(list)

    def add(name, inst, s, sign):
        events[(name, inst)].append((s, inst, sign))
        events[(name, "ALLE")].append((s, inst, sign))

    jumps = {}
    for inst, series in grid.items():
        moves = [0.0] * len(series)
        for s in range(1, len(series)):
            if series[s] and series[s - 1]:
                moves[s] = series[s][0] - series[s - 1][0]
        typical = sqrt(sum(m * m for m in moves) / max(1, len(moves)))
        jumps[inst] = (moves, max(0.3, 3 * typical))
        for s, row in enumerate(series):
            if not row:
                continue
            _, _, imb, gap = row
            if abs(imb) >= 0.5:
                add("Orderbuch stark einseitig", inst, s, 1 if imb > 0 else -1)
            if gap is not None and abs(gap) >= 0.15:
                add("Mitte der grossen Orders weicht ab", inst, s, 1 if gap > 0 else -1)
            if abs(moves[s]) >= jumps[inst][1]:
                add("Ploetzlicher Sprung", inst, s, 1 if moves[s] > 0 else -1)

    for leader, (moves, limit) in jumps.items():
        for s, m in enumerate(moves):
            if abs(m) >= limit:
                for follower in grid:
                    if follower != leader:
                        name = f"{leader} springt -> {follower}"
                        events[(name, follower)].append((s, follower, 1 if m > 0 else -1))

    for s, inst, vol, side, buyer, seller in trades:
        if inst not in grid:
            continue
        sign = 1 if side == "bid" else -1
        active = buyer if side == "bid" else seller
        if active:
            add(f"{active} handelt aktiv", inst, s, sign)
        if vol >= 20:
            add("Grosser Trade (ab 20)", inst, s, sign)
    return events


def stats(values):
    n = len(values)
    mean = sum(values) / n
    var = sum((v - mean) ** 2 for v in values) / max(1, n - 1)
    t = mean / sqrt(var / n) if var > 0 else (99.0 if mean else 0.0)
    return mean, t


def measure(grid, evs, horizon, end):
    """Effekt in der 1. und 2. Haelfte. Ereignisse ueberlappen nicht."""
    halves = ([], [])
    costs = []
    next_free = {}
    for s, inst, sign in sorted(evs):
        entry, exit_ = s + DELAY, s + DELAY + horizon
        if exit_ > end or s < next_free.get(inst, 0):
            continue
        a, b = grid[inst][entry], grid[inst][exit_]
        if not a or not b:
            continue
        next_free[inst] = exit_
        halves[0 if exit_ <= end // 2 else 1].append(sign * (b[0] - a[0]))
        costs.append((a[1] + b[1]) / 2)
    return halves, costs


def analyse(grid, trades, end):
    results = []
    for (name, scope), evs in find_events(grid, trades).items():
        for h in HORIZONS:
            (first, second), costs = measure(grid, evs, h, end)
            if len(first) < MIN_EVENTS or len(second) < MIN_EVENTS:
                continue
            m1, t1 = stats(first)
            if abs(t1) < MIN_T:
                continue
            direction = 1 if m1 > 0 else -1          # aus Haelfte 1 gelernt
            m2, t2 = stats([direction * v for v in second])
            cost = sum(costs) / len(costs)
            net = m2 - cost
            if t2 < MIN_T:
                verdict = "NICHT STABIL"
            elif net > 0:
                verdict = "HANDELBAR"
            else:
                verdict = "NUR FAIR VALUE"
            results.append(dict(muster=name, aktie=scope, sekunden=h,
                                reaktion="mit dem Signal" if direction > 0 else "gegen das Signal",
                                faelle=len(first) + len(second), effekt_h1=round(abs(m1), 3),
                                effekt_h2=round(m2, 3), kosten=round(cost, 3),
                                netto=round(net, 3), urteil=verdict))
    order = {"HANDELBAR": 0, "NUR FAIR VALUE": 1, "NICHT STABIL": 2}
    results.sort(key=lambda r: (order[r["urteil"]], -r["netto"]))
    return results


def write(folder, results, end):
    cols = ["urteil", "muster", "aktie", "sekunden", "reaktion", "faelle",
            "effekt_h1", "effekt_h2", "kosten", "netto"]
    with open(os.path.join(folder, "muster.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols)
        w.writeheader()
        w.writerows(results)

    lines = [f"# Muster (Stand {datetime.now():%d.%m. %H:%M}, {end / 60:.0f} Minuten Daten)", "",
             "- HANDELBAR: in beiden Haelften da UND groesser als der Spread -> sofort handeln (IOC)",
             "- NUR FAIR VALUE: stabil, aber kleiner als der Spread -> nur eigene Preise verschieben",
             "- NICHT STABIL: nur in der 1. Haelfte -> ignorieren", "",
             "Effekt und Netto = Preis pro Stueck. Es werden sehr viele Muster getestet,",
             "einige wirken nur durch Zufall. Vertraut dem, was auch mit neuen Daten bleibt.", ""]
    for verdict in ("HANDELBAR", "NUR FAIR VALUE", "NICHT STABIL"):
        group = [r for r in results if r["urteil"] == verdict]
        lines.append(f"## {verdict} ({len(group)})")
        if group:
            lines += ["| Muster | Aktie | nach | Preis laeuft | Faelle | Effekt 2. Haelfte | Netto |",
                      "|---|---|---|---|---|---|---|"]
            lines += [f"| {r['muster']} | {r['aktie']} | {r['sekunden']}s | {r['reaktion']} | "
                      f"{r['faelle']} | {r['effekt_h2']:+.3f} | {r['netto']:+.3f} |" for r in group[:15]]
        lines.append("")
    with open(os.path.join(folder, "muster.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return "\n".join(lines)


def main():
    if len(sys.argv) > 1:
        folder = sys.argv[1]
    else:
        found = sorted(
            [p for p in glob.glob(os.path.join("data", "*")) + glob.glob("data_*") if os.path.isdir(p)],
            key=os.path.getmtime,
        )
        if not found:
            sys.exit("Kein data/ Unterordner gefunden. Erst runner.py --log-csv oder collector.py laufen lassen.")
        folder = found[-1]
    grid, trades, end = load(folder)
    if end < 600:
        print(f"Achtung: erst {end / 60:.0f} Minuten Daten. Mindestens 30 sind sinnvoll.")
    print(write(folder, analyse(grid, trades, end), end))
    print("Gespeichert in", folder)


if __name__ == "__main__":
    main()
