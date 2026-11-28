"""Sprung-Analyse: Springen die Preise nach grossen Spruengen zurueck oder laufen sie weiter?
Start:  python bot/sprung.py data_27_213800
Liest books.csv. Sendet KEINE Orders. Schreibt sprung.md in den Datenordner.

Sprung = Mitte bewegt sich innerhalb von WINDOW Sekunden um mindestens MIN_TICKS Ticks.
Rueckkehr = welcher Anteil des Sprungs ist nach X Sekunden wieder weg
  (+100 % = ganz zurueck, 0 % = bleibt, negativ = laeuft weiter).
"""
import os
import sys
from bisect import bisect_left
from statistics import mean, median

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from wand import load  # noqa: E402

TICK = 0.1
MIN_TICKS = 5          # ab so vielen Ticks gilt eine Bewegung als Sprung
WINDOW = 1.0           # ... innerhalb dieser Sekunden
COOLDOWN = 10.0        # danach erst wieder einen Sprung zaehlen
AFTER = [2, 5, 15, 30]


def mids_of(snaps):
    t, m = [], []
    for ts, b, a in snaps:
        if b and a:
            t.append(ts)
            m.append((b[0][0] + a[0][0]) / 2)
    return t, m


def mid_at(times, mids, t):
    i = bisect_left(times, t)
    if i >= len(times):
        return None
    return mids[max(i - 1, 0)] if times[i] > t and i > 0 else mids[i]


def jumps(times, mids):
    out = []
    last = -1e18
    j = 0
    for i, t in enumerate(times):
        while times[j] < t - WINDOW:
            j += 1
        if t - last < COOLDOWN:
            continue
        lo = min(mids[j:i + 1])
        hi = max(mids[j:i + 1])
        m = mids[i]
        size = 0.0
        if m - lo >= MIN_TICKS * TICK - 1e-9:
            size, pre = m - lo, lo
        elif hi - m >= MIN_TICKS * TICK - 1e-9:
            size, pre = -(hi - m), hi
        if size:
            rev = {}
            for h in AFTER:
                later = mid_at(times, mids, t + h)
                if later is not None and times[-1] >= t + h:
                    rev[h] = (m - later) / size   # Anteil des Sprungs, der zurueckgeht
            out.append({"t": t, "size": size, "pre": pre, "rev": rev})
            last = t
    return out


def main():
    if len(sys.argv) < 2:
        print("Aufruf: python bot/sprung.py data_ORDNER")
        return
    folder = sys.argv[1]
    books, _ = load(folder)
    all_j = []
    lines = [f"# Sprung-Analyse ({folder})", "",
             f"Sprung = mind. {MIN_TICKS} Ticks in {WINDOW:.0f} s. Rueckkehr: +100 % = ganz zurueck, "
             "0 % = bleibt, negativ = laeuft weiter.", "",
             "| Aktie | Spruenge | Median Groesse | " + " | ".join(f"Rueckkehr {h}s" for h in AFTER)
             + " | Anteil zurueck (5s) |",
             "|---|---|---|" + "---|" * len(AFTER) + "---|"]
    for inst in sorted(books):
        t, m = mids_of(books[inst])
        if len(t) < 10:
            continue
        js = jumps(t, m)
        for x in js:
            x["inst"] = inst
        all_j += js
        if not js:
            lines.append(f"| {inst} | 0 | - |" + " - |" * len(AFTER) + " - |")
            continue
        cells = []
        for h in AFTER:
            vals = [x["rev"][h] for x in js if h in x["rev"]]
            cells.append(f"{mean(vals):+.0%}" if vals else "-")
        r5 = [x["rev"][5] for x in js if 5 in x["rev"]]
        share = f"{sum(v > 0 for v in r5) / len(r5):.0%}" if r5 else "-"
        lines.append(f"| {inst} | {len(js)} | {median(abs(x['size']) for x in js):.2f} | "
                     + " | ".join(cells) + f" | {share} |")
    lines += ["", "## Gesamt", ""]
    if all_j:
        all_j.sort(key=lambda x: x["t"])
        half = all_j[len(all_j) // 2]["t"]
        for name, group in (("alle", all_j), ("1. Haelfte", [x for x in all_j if x["t"] < half]),
                            ("2. Haelfte", [x for x in all_j if x["t"] >= half])):
            cells = []
            for h in AFTER:
                vals = [x["rev"][h] for x in group if h in x["rev"]]
                cells.append(f"{h}s: {mean(vals):+.0%}" if vals else f"{h}s: -")
            lines.append(f"- **{name}** ({len(group)} Spruenge): " + ", ".join(cells))
        r5 = [x["rev"][5] for x in all_j if 5 in x["rev"]]
        avg5 = mean(r5) if r5 else 0
        verdict = ("Preise springen ZURUECK -> Spike-Catcher (Gegenrichtung) testen."
                   if avg5 > 0.25 else
                   "Preise laufen WEITER -> eher mit dem Sprung handeln (Momentum)."
                   if avg5 < -0.1 else
                   "Kein klares Verhalten -> Sprung bleibt meist stehen.")
        lines += ["", f"**Fazit:** {verdict}"]
        lines += ["", "Groesste Spruenge:", ""]
        for x in sorted(all_j, key=lambda x: -abs(x["size"]))[:8]:
            rv = ", ".join(f"{h}s {x['rev'][h]:+.0%}" for h in AFTER if h in x["rev"])
            lines.append(f"- {x['inst']}: {x['size']:+.2f} (Rueckkehr {rv})")
    else:
        lines.append("Keine Spruenge gefunden.")
    text = "\n".join(lines) + "\n"
    with open(os.path.join(folder, "sprung.md"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
