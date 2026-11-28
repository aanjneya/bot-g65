# Backtest: runner:BacktestBot auf data_27_213800

Dauer: 22.2 min. PnL bewertet zur Mitte des Buchs.

| Kennzahl | Wert |
|---|---|
| PnL gesamt | -163.7 |
| 1. Haelfte / 2. Haelfte | -138.6 / -25.1 |
| Groesster Rueckgang | -197.9 |
| Fills (passiv / aggressiv) | 227 / 16 |
| Max. Summe aller Positionen | 31 |
| Max. Anfragen pro Sekunde | 15 |

## Pro Aktie

| Aktie | PnL | Fills | Endposition | Markout 1s | Markout 5s |
|---|---|---|---|---|---|
| AMD | -59.5 | 28 | -5 | -0.355 | -0.369 |
| AVGO | -16.3 | 38 | 0 | -0.093 | -0.145 |
| CRWV | +14.7 | 45 | -3 | +0.027 | +0.271 |
| MU | -102.6 | 7 | 0 | -1.650 | -1.643 |
| NBIS | +32.6 | 55 | 5 | +0.107 | +0.105 |
| NVDA | -9.5 | 20 | 0 | -0.125 | -0.142 |
| SNDK | +11.9 | 33 | 2 | +0.062 | +0.067 |
| VRT | -35.0 | 17 | 0 | -0.385 | -0.394 |

Markout = Preisbewegung nach unserem Fill zu unseren Gunsten (positiv = gut).
Negativer Markout = Adverse Selection: wir werden vor allem dann gefuellt, wenn der
Preis danach gegen uns laeuft.
Grenzen: unsere Orders bewegen den abgespielten Markt nicht; IOCs treffen das Buch
ohne Verzoegerung. Fuer kleine Mengen ist das brauchbar, fuer grosse zu optimistisch.
