"""Historische Optibook-Daten holen und ins Format von collector.py schreiben,
damit backtest.py, wand.py, muster.py und folge.py sie genauso lesen koennen.
Laeuft nur im Dev Environment (dort sind die Zugangsdaten). Sendet KEINE Orders.

Start:
    python bot/hist_export.py --last 60                       # letzte 60 Minuten
    python bot/hist_export.py --start "2026-11-27 20:00" --end "2026-11-27 21:00"
Danach:
    python bot/backtest.py data_hist_<...> --strategy wall

Hinweis: Zeiten so angeben, wie die Uhr im Dev Environment laeuft (dort UTC,
also 1 Stunde hinter Berlin). Pro Aufruf hoechstens 1 Tag. Die API erlaubt
30 Anfragen pro Minute; Downloads werden zwischengespeichert.
"""
import argparse
import csv
import json
import os
from datetime import datetime, timedelta


def parse_levels(raw):
    """bids_json/asks_json in [(preis, menge)] umwandeln. Akzeptiert mehrere Formate."""
    if raw is None:
        return []
    data = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
    out = []
    for lv in data or []:
        if isinstance(lv, dict):
            p = lv.get("price", lv.get("p"))
            v = lv.get("volume", lv.get("v", lv.get("quantity")))
        else:
            p, v = lv[0], lv[1]
        if p is not None and v:
            out.append((round(float(p), 1), int(v)))
    return out


def fmt(levels):
    return "|".join(f"{p:.1f}:{v}" for p, v in levels)


def iso(ns):
    return datetime.fromtimestamp(int(ns) / 1e9).isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--last", type=int, help="die letzten N Minuten")
    ap.add_argument("--start", help='z. B. "2026-11-27 20:00"')
    ap.add_argument("--end", help='z. B. "2026-11-27 21:00"')
    args = ap.parse_args()
    if args.last:
        end = datetime.now()
        start = end - timedelta(minutes=args.last)
    elif args.start and args.end:
        start = datetime.fromisoformat(args.start)
        end = datetime.fromisoformat(args.end)
    else:
        ap.error("--last N oder --start und --end angeben")

    from optibook.historical_data_loader import HistoricalDataLoader
    loader = HistoricalDataLoader()
    print(f"Lade {start} bis {end} ...")
    try:
        books = loader.get_pricebooks_df(start, end)
        trades = loader.get_trades_df(start, end)
    except Exception as exc:
        print("Laden fehlgeschlagen:", exc)
        print("Bei 502/Timeout: spaeter nochmal oder kleineren Zeitraum waehlen.")
        return

    folder = os.path.join("data", "hist_" + start.strftime("%d_%H%M") + "_" + end.strftime("%H%M"))
    os.makedirs(folder, exist_ok=True)

    n_books = 0
    last = {}
    with open(os.path.join(folder, "books.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time", "instrument", "bids", "asks"])
        for row in books.sort_values("timestamp_ns").itertuples(index=False):
            bids = fmt(sorted(parse_levels(row.bids_json), key=lambda x: -x[0]))
            asks = fmt(sorted(parse_levels(row.asks_json), key=lambda x: x[0]))
            if last.get(row.instrument_id) == (bids, asks):
                continue
            last[row.instrument_id] = (bids, asks)
            w.writerow([iso(row.timestamp_ns), row.instrument_id, bids, asks])
            n_books += 1

    with open(os.path.join(folder, "trades.csv"), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["time", "instrument", "price", "volume", "aggressor_side", "buyer", "seller"])
        for row in trades.sort_values("timestamp_ns").itertuples(index=False):
            w.writerow([iso(row.timestamp_ns), row.instrument_id, row.price, row.volume,
                        row.aggressor_side, getattr(row, "buyer", ""), getattr(row, "seller", "")])
    open(os.path.join(folder, "news.csv"), "w").write("time,post\n")
    print(f"Fertig: {folder}  ({n_books} Buch-Aenderungen, {len(trades)} Trades)")
    if len(books):
        print("Beispiel bids_json:", str(books.iloc[0].bids_json)[:120])


if __name__ == "__main__":
    main()
