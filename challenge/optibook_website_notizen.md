# Optibook-Website: Notizen (Stand Fr 27.11.2026, ca. 22:25)
Quelle: https://2026-hackatum.optibook.net (ohne Login gelesen)

## Harte Regeln
- Aktien: AMD, AVGO, CRWV, MU, NBIS, NVDA, SNDK, VRT
- Tick Size: 0.10 (Preise nur in 10-Cent-Schritten)
- Positionslimit: +/-100 pro Aktie
- Offenes Ordervolumen: max. 200 pro Aktie
- Max. 25 Requests/Sekunde (insert, amend, delete) über alle Aktien, sonst Disconnect
- Eine Verbindung pro Team; neue Verbindung wirft die alte raus
- Programmende: offene Limit-Orders werden gelöscht, Positionen bleiben
- Abschluss-Wettbewerb "hilft" den Gesamtsieger zu bestimmen

## Zugang (/setup-instructions)
1. Sign up und Team beitreten
2. Auf der Teams-Seite oben rechts "Dev Environment" (VS Code im Browser, Client und Zugangsdaten fertig)
3. your_optiver_workspace/optibook_guides/code_snippets.ipynb öffnen, instrument_id ersetzen, Zellen ausführen

## Übungen (/intro-exercises)
Set A: 10 Lots kaufen und Position prüfen; wieder verkaufen; PnL selbst rechnen (e.get_pnl());
Order über 100000 einstellen, auf 5 ändern, löschen; alle Public Trades ausgeben.
Set B: Orderbuch ausgeben; eigene PnL-Funktion (auch Short); strukturierter Algorithmus
(Order nicht ausgeführt, Limit, Disconnect, Fehler, Abweichung der Performance).
Tipp der Seite: erst mit 1 Lot testen.

## FAQ
- "already connected": weitermachen, ihr seid verbunden
- Trade Ticks nach Connect leer: Client hört erst ab Verbindung
- Keine Benachrichtigung bei Ausführung: poll_new_trades oder Position prüfen

## Docs (/docs)
- Module: common_types, synchronous_client, exchange_responses, exporter
- Wichtig: get_last_price_book, insert_order(limit|ioc), amend_order, delete_order(s),
  get_positions, get_pnl, poll_new_trades, poll_new_trade_ticks (buyer/seller/aggressor_side),
  poll_new_social_media_feeds
- Historical Data Loader steht in challenge/api_reference.md, nicht in den Website-Docs

## Lectures (/lectures, YouTube)
1 Financial Markets: https://www.youtube.com/watch?v=agJUYbpcXxY
2 Market Participants: https://www.youtube.com/watch?v=gNomxbBBIVA
3 Order Books: https://www.youtube.com/watch?v=Yt-45z3reiw
4 Pairs Trading (relevant): https://www.youtube.com/watch?v=4isu0n1GOWU
5 Portfolio Values: https://www.youtube.com/watch?v=OFIwLbmqzY0
(Option Theory: nur Aktien im Wettbewerb)

## Visualizer (/visualiser), Momentaufnahme
- Öffentlich sichtbar: Leaderboard (PnL, Position je Team), Orderbuch, Trades MIT Käufer/Verkäufer
- Namen, die handeln, aber nicht in der Teamliste stehen (vermutlich Bots):
  JazzyJellies, PrudentPoultry, GroovyGrapefruits, LivelyLasagnas

## Beobachtungen / Hypothesen (UNGEPRÜFT, kurzer Ausschnitt)
- JazzyJellies und PrudentPoultry wirken wie aktive Käufer/Verkäufer (Aggressor), handeln in beide Richtungen
  -> falls zufällig: gut für Market Making
- CRWV-Orderbuch: sehr große Orders (ca. 500-1000) in mehreren Preisstufen
  -> Mitte dazwischen evtl. guter Fair Value
- CRWV-Chart: sprunghafte Bewegungen -> Ursache prüfen (News-Feed?)
