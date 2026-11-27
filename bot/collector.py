"""Datensammler fuer Optibook. Liest nur mit, sendet KEINE Orders.
Start:  python collector.py      Stop: Ctrl+C
Ergebnis: Ordner data_<Uhrzeit>/ mit books.csv, trades.csv, news.csv
und erkenntnisse.md (wird jede Minute neu geschrieben). Braucht insights.py.
"""
import csv
import logging
import os
import time
from datetime import datetime

from optibook.synchronous_client import Exchange

from insights import Analyzer

logging.getLogger("client").setLevel("ERROR")

INSTRUMENTS = ["AMD", "AVGO", "CRWV", "MU", "NBIS", "NVDA", "SNDK", "VRT"]
INTERVAL = 0.25      # Sekunden zwischen zwei Durchlaeufen
REPORT_EVERY = 60    # Sekunden zwischen zwei Auswertungen


def levels(side):
    """Alle Preisstufen als Text: preis:menge|preis:menge"""
    return "|".join(f"{lv.price:.1f}:{lv.volume}" for lv in side)


def save_report(analyzer, folder):
    """Fehler in der Auswertung duerfen das Sammeln nicht stoppen."""
    try:
        text = analyzer.report()
        with open(os.path.join(folder, "erkenntnisse.md"), "w", encoding="utf-8") as f:
            f.write(text)
        # Verlauf behalten, um zu sehen ob ein Muster stabil bleibt
        with open(os.path.join(folder, "erkenntnisse_verlauf.md"), "a", encoding="utf-8") as f:
            f.write(text + "\n---\n")
    except Exception as ex:
        print("Auswertung fehlgeschlagen:", ex)


def main():
    folder = os.path.join("data", "collector_" + datetime.now().strftime("%d_%H%M%S"))
    os.makedirs(folder, exist_ok=True)
    files = [open(os.path.join(folder, n), "w", newline="", encoding="utf-8")
             for n in ("books.csv", "trades.csv", "news.csv")]
    books, trades, news = (csv.writer(f) for f in files)
    books.writerow(["time", "instrument", "bids", "asks"])
    trades.writerow(["time", "instrument", "price", "volume", "aggressor_side", "buyer", "seller"])
    news.writerow(["time", "post"])

    e = Exchange()
    e.connect()
    print("Verbunden. Schreibe nach", folder)

    last_book = {}
    n_trades = n_news = 0
    last_status = last_report = time.time()
    analyzer = Analyzer(INSTRUMENTS)
    try:
        while e.is_connected():
            now = datetime.now().isoformat()
            for i in INSTRUMENTS:
                book = e.get_last_price_book(i)
                if book is not None:
                    analyzer.add_book(i, [(l.price, l.volume) for l in book.bids],
                                      [(l.price, l.volume) for l in book.asks])
                    row = (levels(book.bids), levels(book.asks))
                    if row != last_book.get(i):  # nur Aenderungen speichern
                        books.writerow([now, i, *row])
                        last_book[i] = row
                for t in e.poll_new_trade_ticks(i):
                    trades.writerow([now, i, t.price, t.volume,
                                     t.aggressor_side, t.buyer, t.seller])
                    n_trades += 1
                    analyzer.add_trade(time.time(), i, t.price, t.volume,
                                       t.aggressor_side, t.buyer, t.seller)
            for p in e.poll_new_social_media_feeds():
                news.writerow([now, p.post])
                print("NEWS:", p.post)
                n_news += 1
                analyzer.add_news(time.time(), p.post)
            analyzer.tick(time.time())
            if time.time() - last_report >= REPORT_EVERY:
                save_report(analyzer, folder)
                last_report = time.time()
            if time.time() - last_status >= 30:
                for f in files:
                    f.flush()
                print(f"{datetime.now():%H:%M:%S}  Trades: {n_trades}  News: {n_news}")
                last_status = time.time()
            time.sleep(INTERVAL)
        print("Verbindung verloren (hat jemand aus dem Team neu verbunden?)")
    except KeyboardInterrupt:
        print("Gestoppt.")
    finally:
        save_report(analyzer, folder)
        for f in files:
            f.close()
        e.disconnect()


if __name__ == "__main__":
    main()
