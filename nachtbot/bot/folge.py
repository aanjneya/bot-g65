"""Folge-Aktien-Analyse mit echten Preisen. Liest nur books.csv, sendet KEINE Orders.
Start:  python folge.py data_xyz     (ohne Angabe: neuester data_ Ordner)

Gedachter Trade: Aktie A springt -> nach DELAY Sekunden Aktie B in dieselbe
Richtung handeln (kaufen zum Ask / verkaufen zum Bid), nach HOLD Sekunden
zum Gegenpreis aussteigen. Nur die Menge am besten Preis, max. LOTS.
"""
import csv
import glob
import os
import sys
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime
from math import sqrt

LOTS = 10
DELAYS = [0.25, 0.5, 1.0, 2.0]
HOLDS = [2, 5, 15]
MAIN_DELAY, MAIN_HOLD = 0.5, 5     # fest gewaehlt, nicht auf die Daten optimiert
MIN_EVENTS = 8
COOLDOWN = 5.0                     # s zwischen zwei Spruengen derselben Aktie


def best(text):
    if not text:
        return None
    p, v = text.split("|", 1)[0].split(":")
    return float(p), int(v)


def load(folder):
    data = defaultdict(lambda: ([], []))      # aktie -> (zeiten, (bid, bidmenge, ask, askmenge))
    with open(os.path.join(folder, "books.csv"), newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                t = datetime.fromisoformat(row["time"]).timestamp()
                b, a = best(row["bids"]), best(row["asks"])
            except (ValueError, TypeError, KeyError, AttributeError):
                continue                      # halb geschriebene Zeile
            times, tops = data[row["instrument"]]
            times.append(t)
            tops.append((*b, *a) if b and a else None)
    return dict(data)


def at(series, t):
    times, tops = series
    k = bisect_right(times, t) - 1
    return tops[k] if k >= 0 else None


def mid(top):
    return (top[0] + top[2]) / 2


def find_jumps(series):
    """Spruenge: Mitte bewegt sich innerhalb 1 s ungewoehnlich stark."""
    times, tops = series
    moves = []
    for t, top in zip(times, tops):
        before = at(series, t - 1.0)
        if top and before:
            moves.append((t, mid(top) - mid(before)))
    if not moves:
        return []
    ordered = sorted(abs(m) for _, m in moves)
    limit = max(0.5, 4 * ordered[len(ordered) // 2], ordered[int(len(ordered) * 0.98)])
    jumps, last = [], -1e18
    for t, m in moves:
        if abs(m) >= limit and t - last >= COOLDOWN:
            jumps.append((t, m))
            last = t
    return jumps


def trade(series, t, sign, delay, hold, end):
    """Gewinn in Geld, oder None wenn nicht handelbar."""
    t_in, t_out = t + delay, t + delay + hold
    if t_out > end:
        return None
    a, b = at(series, t_in), at(series, t_out)
    if not a or not b:
        return None
    if sign > 0:
        return (b[0] - a[2]) * min(LOTS, a[3], b[1])
    return (a[0] - b[2]) * min(LOTS, a[1], b[3])


def evaluate(data, delay, hold, end, half):
    jumps = {i: find_jumps(s) for i, s in data.items()}
    results = {}
    for a, events in jumps.items():
        for b, series in data.items():
            if a == b:
                continue
            first, second = [], []
            for t, m in events:
                pnl = trade(series, t, 1 if m > 0 else -1, delay, hold, end)
                if pnl is not None:
                    (first if t < half else second).append(pnl)
            if first or second:
                results[(a, b)] = (first, second)
    return jumps, results


def verdict(first, second):
    n = len(first) + len(second)
    if n < MIN_EVENTS or len(first) < 3 or len(second) < 3:
        return "ZU WENIG FAELLE"
    if sum(first) > 0 and sum(second) > 0:
        allv = first + second
        mean = sum(allv) / n
        sd = sqrt(sum((v - mean) ** 2 for v in allv) / (n - 1))
        return "KANDIDAT" if sd == 0 or mean / (sd / sqrt(n)) >= 2 else "UNSICHER"
    return "NEIN"


def main():
    if len(sys.argv) > 1:
        folder = sys.argv[1]
    else:
        found = sorted(glob.glob("data_*"), key=os.path.getmtime)
        if not found:
            sys.exit("Kein data_ Ordner gefunden.")
        folder = found[-1]
    data = load(folder)
    if not data:
        sys.exit("Keine Daten in books.csv")
    start = min(s[0][0] for s in data.values())
    end = max(s[0][-1] for s in data.values())
    minutes = max((end - start) / 60, 1e-9)
    half = (start + end) / 2

    jumps, results = evaluate(data, MAIN_DELAY, MAIN_HOLD, end, half)
    rows = []
    for (a, b), (first, second) in results.items():
        allv = first + second
        total = sum(allv)
        rows.append((verdict(first, second), total * 30 / minutes, a, b, len(allv),
                     total / len(allv), sum(v > 0 for v in allv) / len(allv)))
    order = {"KANDIDAT": 0, "UNSICHER": 1, "ZU WENIG FAELLE": 2, "NEIN": 3}
    rows.sort(key=lambda r: (order[r[0]], -r[1]))
    shown = [r for r in rows if r[1] > 0][:12]

    out = [f"# Folge-Aktien ({minutes:.0f} Minuten, Verzoegerung {MAIN_DELAY}s, halten {MAIN_HOLD}s, max {LOTS} Stueck)",
           "", "Spruenge je Aktie: " + ", ".join(f"{i} {len(j)}" for i, j in sorted(jumps.items())),
           "", "| Urteil | Wenn | dann | Faelle | Gewinn je Fall | Treffer | Gewinn je 30 min |",
           "|---|---|---|---|---|---|---|"]
    out += [f"| {v} | {a} | {b} | {n} | {avg:+.2f} | {hit:.0%} | {per30:+.0f} |"
            for v, per30, a, b, n, avg, hit in shown]
    if not shown:
        out.append("| nichts mit Gewinn |  |  |  |  |  |  |")

    out += ["", "## Wie schnell muss der Bot sein? (Gewinn gesamt, 5 beste Paare)",
            "| Paar | haltezeit | " + " | ".join(f"{d}s" for d in DELAYS) + " |",
            "|---|---|" + "---|" * len(DELAYS)]
    grid = {(d, h): evaluate(data, d, h, end, half)[1] for d in DELAYS for h in HOLDS}
    for r in shown[:5]:
        pair = (r[2], r[3])
        for h in HOLDS:
            cells = []
            for d in DELAYS:
                first, second = grid[(d, h)].get(pair, ([], []))
                cells.append(f"{sum(first) + sum(second):+.0f}")
            out.append(f"| {pair[0]} -> {pair[1]} | {h}s | " + " | ".join(cells) + " |")

    out += ["", "KANDIDAT = in beiden Haelften Gewinn und deutlich. Trotzdem auf neuen Daten pruefen:",
            "bei 56 Paaren sieht eines leicht durch Zufall gut aus. Die Sprunggrenze nutzt alle",
            "Daten (im Live-Bot erst aus der Vergangenheit berechnen)."]
    text = "\n".join(out) + "\n"
    with open(os.path.join(folder, "folge.md"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
