"""Wand-Analyse: Lohnt sich Penny Jumping vor grossen Orders?
Start:  python bot/wand.py data_27_213800
Liest books.csv und trades.csv (vom collector.py). Sendet KEINE Orders.
Schreibt wand.md in den Datenordner.

Idee: Steht eine grosse Kauf-Order (Wand) im Buch, kaufen wir 1 Tick darueber.
Faellt der Preis, verkaufen wir an die Wand und verlieren nur 1 Tick.
Spiegelbildlich fuer Verkaufs-Waende.
"""
import csv
import os
import sys
from bisect import bisect_left
from datetime import datetime
from statistics import median

TICK = 0.1
WALL = 400          # ab dieser Menge gilt eine Stufe als Wand
MAX_DIST = 3        # Wand hoechstens so viele Ticks vom besten Preis entfernt
LOT = 10
HOLDS = [1, 5, 15]  # Sekunden halten, bevor wir aussteigen
BREAK_WAIT = 5      # Sekunden: haelt die Wand, wenn der Preis sie erreicht?


def ts(text):
    return datetime.fromisoformat(text).timestamp()


def parse_side(text):
    out = []
    for part in (text or "").split("|"):
        if ":" in part:
            p, v = part.split(":")
            out.append((round(float(p), 1), int(float(v))))
    return out


def load(folder):
    books, trades = {}, {}
    with open(os.path.join(folder, "books.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            books.setdefault(r["instrument"], []).append(
                (ts(r["time"]), parse_side(r["bids"]), parse_side(r["asks"])))
    path = os.path.join(folder, "trades.csv")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                trades.setdefault(r["instrument"], []).append(
                    (ts(r["time"]), round(float(r["price"]), 1), int(float(r["volume"]))))
    for d in (books, trades):
        for k in d:
            d[k].sort(key=lambda x: x[0])
    return books, trades


def find_wall(levels, best, side):
    """Naechste Stufe mit Menge >= WALL, hoechstens MAX_DIST Ticks vom besten Preis."""
    for p, v in levels:
        dist = (best - p) if side == "bid" else (p - best)
        if dist > MAX_DIST * TICK + 1e-9:
            break
        if v >= WALL:
            return p, v
    return None


def volume_at(levels, price):
    for p, v in levels:
        if abs(p - price) < 1e-9:
            return v
    return 0


def mirror(snaps, trades):
    """Verkaufsseite als Kaufseite rechnen: alle Preise negieren."""
    ms = [(t, [(-p, v) for p, v in a], [(-p, v) for p, v in b]) for t, b, a in snaps]
    mt = [(t, -p, v) for t, p, v in trades]
    return ms, mt


def wall_stats(snaps):
    """Wie oft gibt es eine Wand, wie lange lebt sie, haelt sie?"""
    with_wall, lifetimes, held, broken = 0, [], 0, 0
    cur, start = None, None
    total = max(len(snaps), 1)
    for i, (t, bids, asks) in enumerate(snaps):
        if not bids or not asks:
            continue
        w = find_wall(bids, bids[0][0], "bid")
        if w:
            with_wall += 1
        price = w[0] if w else None
        if price != cur:
            if cur is not None:
                lifetimes.append(t - start)
            cur, start = price, t
        # Preis erreicht die Wand: haelt sie BREAK_WAIT Sekunden?
        if w and abs(bids[0][0] - w[0]) < 1e-9 and i > 0 and snaps[i - 1][1] \
                and snaps[i - 1][1][0][0] > w[0]:
            end = t + BREAK_WAIT
            ok = True
            for t2, b2, _ in snaps[i + 1:]:
                if t2 > end:
                    break
                if not b2 or b2[0][0] < w[0] - 1e-9:
                    ok = False
                    break
            held += ok
            broken += not ok
    return with_wall / total, lifetimes, held, broken


def simulate(snaps, trades, hold, allow=None):
    """Kaufen 1 Tick ueber der Wand, raus nach `hold` Sekunden oder sofort an die
    Wand, wenn sie verschwindet. `allow(i)` = Filter: darf bei Snapshot i quotiert werden?
    Gibt Liste (gewinn_pessimistisch, gewinn_mid, scratch, fill_zeit)."""
    times = [s[0] for s in snaps]
    tr_times = [t[0] for t in trades]
    results = []
    i = 0
    n = len(snaps)
    while i < n - 1:
        t, bids, asks = snaps[i]
        if not bids or not asks:
            i += 1
            continue
        w = find_wall(bids, bids[0][0], "bid")
        if not w or (allow is not None and not allow(i)):
            i += 1
            continue
        wall_p = w[0]
        our_p = round(wall_p + TICK, 1)
        if our_p >= asks[0][0] - 1e-9:
            i += 1
            continue
        queue = volume_at(bids, our_p)  # Menge vor uns auf unserem Preis
        # Order steht, bis der naechste Snapshot kommt; Fills ueber Trades
        t_next = snaps[i + 1][0]
        j = bisect_left(tr_times, t)
        filled_at = None
        while j < len(trades) and trades[j][0] <= t_next:
            _, p, v = trades[j]
            if p < our_p - 1e-9:
                filled_at = trades[j][0]
                break
            if abs(p - our_p) < 1e-9:
                queue -= v
                if queue < 0:
                    filled_at = trades[j][0]
                    break
            j += 1
        if filled_at is None:
            i += 1
            continue
        # Position haben wir. Ausstieg suchen.
        k = bisect_left(times, filled_at)
        exit_p = mid_p = None
        scratch = False
        while k < n:
            tk, bk, ak = snaps[k]
            if bk and ak:
                wall_gone = volume_at(bk, wall_p) < WALL / 2 and bk[0][0] <= our_p + 1e-9
                if wall_gone or bk[0][0] < wall_p - 1e-9:
                    exit_p = bk[0][0]          # Schutz weg: sofort verkaufen
                    mid_p = exit_p
                    scratch = True
                    break
                if tk - filled_at >= hold:
                    exit_p = bk[0][0]          # pessimistisch: an den Bid verkaufen
                    mid_p = (bk[0][0] + ak[0][0]) / 2
                    break
            k += 1
        if exit_p is None:
            break
        results.append((exit_p - our_p, mid_p - our_p, scratch, filled_at))
        i = max(k, i + 1)
    return results


def fmt(x):
    return f"{x:+.3f}"


# ---------- Filter: nur quoten, wenn die Signale nicht "faellt gleich" sagen ----------

LEADERS = {"SNDK": "NVDA", "CRWV": "MU", "NBIS": "AVGO"}  # aus der Lead-Lag-Analyse
WIN = 2.0     # Sekunden Rueckblick fuer OFI, Leader und Trend
CAP = 100     # Mengen kappen, damit die Wand selbst das Signal nicht dominiert
FILTER_HOLD = 5


def features(snaps, leader_snaps=None):
    """Pro Snapshot: Microprice-Neigung, OFI, Leader-Bewegung, eigener Trend (alle als
    'positiv = gut fuer Kaeufer')."""
    times = [s[0] for s in snaps]
    mids = [(b[0][0] + a[0][0]) / 2 if b and a else None for _, b, a in snaps]
    cum = [0.0]
    for k in range(1, len(snaps)):
        _, b0, a0 = snaps[k - 1]
        _, b1, a1 = snaps[k]
        e = 0.0
        if b0 and a0 and b1 and a1:
            pb0, qb0 = b0[0][0], min(b0[0][1], CAP)
            pb1, qb1 = b1[0][0], min(b1[0][1], CAP)
            pa0, qa0 = a0[0][0], min(a0[0][1], CAP)
            pa1, qa1 = a1[0][0], min(a1[0][1], CAP)
            e = (qb1 if pb1 >= pb0 else 0) - (qb0 if pb1 <= pb0 else 0) \
                - (qa1 if pa1 <= pa0 else 0) + (qa0 if pa1 >= pa0 else 0)
        cum.append(cum[-1] + e)
    if leader_snaps:
        lt = [s[0] for s in leader_snaps]
        lm = [(b[0][0] + a[0][0]) / 2 if b and a else None for _, b, a in leader_snaps]
    out = []
    for k, (t, b, a) in enumerate(snaps):
        f = {"micro": 0.0, "ofi": 0.0, "lead": 0.0, "trend": 0.0}
        if b and a:
            qb, qa = min(b[0][1], CAP), min(a[0][1], CAP)
            micro = (b[0][0] * qa + a[0][0] * qb) / (qb + qa)
            f["micro"] = micro - mids[k]
        j = max(bisect_left(times, t - WIN), 0)
        f["ofi"] = cum[k] - cum[j]
        if mids[k] is not None and mids[j] is not None:
            f["trend"] = mids[k] - mids[j]
        if leader_snaps:
            a_i = bisect_left(lt, t) - 1
            b_i = bisect_left(lt, t - WIN) - 1
            if a_i >= 0 and b_i >= 0 and lm[a_i] is not None and lm[b_i] is not None:
                f["lead"] = lm[a_i] - lm[b_i]
        out.append(f)
    return out


FILTERS = {
    "ohne Filter": lambda f: True,
    "Microprice": lambda f: f["micro"] >= 0,
    "OFI": lambda f: f["ofi"] >= 0,
    "Leader": lambda f: f["lead"] > -TICK / 2,
    "Trend": lambda f: f["trend"] >= 0,
    "Micro+OFI": lambda f: f["micro"] >= 0 and f["ofi"] >= 0,
    "alle 4": lambda f: f["micro"] >= 0 and f["ofi"] >= 0 and f["lead"] > -TICK / 2
                        and f["trend"] >= 0,
}


def filter_section(books, trades):
    all_t = sorted(s[0] for v in books.values() for s in v)
    split = all_t[len(all_t) // 2] if all_t else 0
    rows = {name: [] for name in FILTERS}
    per_inst = {name: {} for name in FILTERS}
    for inst in sorted(books):
        snaps, tr = books[inst], trades.get(inst, [])
        lead = books.get(LEADERS.get(inst, ""), None)
        for side in ("Kauf", "Verkauf"):
            if side == "Kauf":
                s, t, l = snaps, tr, lead
            else:
                s, t = mirror(snaps, tr)
                l = mirror(lead, [])[0] if lead else None
            feats = features(s, l)
            for name, fn in FILTERS.items():
                res = simulate(s, t, FILTER_HOLD, allow=lambda i, fn=fn: fn(feats[i]))
                rows[name] += res
                d = per_inst[name].setdefault(inst, [0.0, 0])
                d[0] += sum(r[1] for r in res) * LOT
                d[1] += len(res)
    lines = ["", f"## 4. Mit Filter (halten {FILTER_HOLD}s, Summen fuer {LOT} Stueck)", "",
             "Ein Filter ist nur echt, wenn BEIDE Haelften besser sind als ohne Filter.", "",
             "| Filter | Fills | Summe schlecht | Summe mid | mid 1. Haelfte | mid 2. Haelfte | mid je Fill |",
             "|---|---|---|---|---|---|---|"]
    for name, res in rows.items():
        if not res:
            lines.append(f"| {name} | 0 | - | - | - | - | - |")
            continue
        bad = sum(r[0] for r in res) * LOT
        mid = sum(r[1] for r in res) * LOT
        h1 = sum(r[1] for r in res if r[3] < split) * LOT
        h2 = sum(r[1] for r in res if r[3] >= split) * LOT
        lines.append(f"| {name} | {len(res)} | {bad:+.1f} | {mid:+.1f} | {h1:+.1f} | {h2:+.1f} | "
                     f"{mid / len(res):+.2f} |")
    best = max((n for n in rows if n != "ohne Filter" and rows[n]),
               key=lambda n: sum(r[1] for r in rows[n]), default=None)
    if best:
        lines += ["", f"### Bester Filter pro Aktie: {best} (Summe mid / Fills)", "",
                  "| Aktie | " + " | ".join(sorted(per_inst[best])) + " |",
                  "|---|" + "---|" * len(per_inst[best]),
                  "| Ergebnis | " + " | ".join(f"{v[0]:+.0f} / {v[1]}" for _, v in
                                             sorted(per_inst[best].items())) + " |"]
    return lines


def main():
    if len(sys.argv) < 2:
        print("Aufruf: python bot/wand.py data_ORDNER")
        return
    folder = sys.argv[1]
    books, trades = load(folder)
    lines = [f"# Wand-Analyse ({folder})", "",
             f"Wand = Stufe mit mind. {WALL} Stueck, hoechstens {MAX_DIST} Ticks vom besten Preis.",
             "Gewinn je Stueck. 'schlecht' = Ausstieg sofort an den Bid/Ask (zahlt Spread),",
             "'mid' = Wert zur Mitte (passiver Ausstieg waere dazwischen).", "",
             "## 1. Gibt es Waende und halten sie?", "",
             "| Aktie | Seite | Zeit mit Wand | Wand-Dauer nahe Preis (Median s) | Preis erreicht Wand: haelt | bricht |",
             "|---|---|---|---|---|---|"]
    sims = []
    for inst in sorted(books):
        snaps, tr = books[inst], trades.get(inst, [])
        for side in ("Kauf", "Verkauf"):
            s, t = (snaps, tr) if side == "Kauf" else mirror(snaps, tr)
            share, life, held, broken = wall_stats(s)
            lm = f"{median(life):.0f}" if life else "-"
            lines.append(f"| {inst} | {side} | {share:.0%} | {lm} | {held} | {broken} |")
            for h in HOLDS:
                sims.append((inst, side, h, simulate(s, t, h)))

    lines += ["", "## 2. Was haette Penny Jumping verdient?", "",
              "| Aktie | Seite | halten | Fills | Gewinn/Stueck schlecht | mid | Scratch-Anteil | Summe mid (10 Stueck) |",
              "|---|---|---|---|---|---|---|---|"]
    totals = {}
    for inst, side, h, res in sims:
        if not res:
            continue
        bad = sum(r[0] for r in res) / len(res)
        mid = sum(r[1] for r in res) / len(res)
        sc = sum(r[2] for r in res) / len(res)
        tot = sum(r[1] for r in res) * LOT
        totals.setdefault(h, [0.0, 0.0, 0])
        totals[h][0] += sum(r[0] for r in res) * LOT
        totals[h][1] += tot
        totals[h][2] += len(res)
        lines.append(f"| {inst} | {side} | {h}s | {len(res)} | {fmt(bad)} | {fmt(mid)} | {sc:.0%} | {tot:+.1f} |")

    lines += ["", "## 3. Gesamt", "", "| halten | Fills | Summe schlecht | Summe mid |", "|---|---|---|---|"]
    for h in HOLDS:
        if h in totals:
            b, m, n = totals[h]
            lines.append(f"| {h}s | {n} | {b:+.1f} | {m:+.1f} |")
    lines += filter_section(books, trades)
    lines += ["", "Lesehilfe: Ist 'mid' klar positiv und 'Scratch-Anteil' niedrig, lohnt sich die",
              "Strategie. Ist schon 'schlecht' positiv, ist sie sehr stark.",
              "Fills sind geschaetzt (wir stehen hinter der Menge, die schon auf unserem Preis lag)."]
    text = "\n".join(lines) + "\n"
    with open(os.path.join(folder, "wand.md"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()
