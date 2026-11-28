# Backtest: runner:BacktestBot auf /home/workspace/data/live_28_011232_216881

Dauer: 30.6 min. PnL bewertet zur Mitte des Buchs.

| Kennzahl | Wert |
|---|---|
| PnL gesamt | -351.3 |
| 1. Haelfte / 2. Haelfte | -205.3 / -146.0 |
| Groesster Rueckgang | -370.4 |
| Fills (passiv / aggressiv) | 589 / 33 |
| Max. Summe aller Positionen | 38 |
| Max. Anfragen pro Sekunde | 17 |

## Pro Aktie

| Aktie | PnL | Fills | Endposition | Markout 1s | Markout 5s |
|---|---|---|---|---|---|
| AMD | -141.0 | 83 | 0 | -0.239 | -0.231 |
| AVGO | -55.0 | 85 | 0 | -0.065 | -0.065 |
| CRWV | +29.4 | 166 | 3 | +0.079 | +0.099 |
| MU | -47.0 | 25 | 0 | -0.368 | -0.350 |
| NBIS | +1.0 | 128 | -1 | +0.032 | +0.042 |
| NVDA | -59.0 | 43 | 3 | -0.678 | -0.685 |
| SNDK | -46.1 | 74 | -3 | -0.005 | -0.128 |
| VRT | -33.3 | 18 | 0 | -0.347 | -0.342 |

Markout = Preisbewegung nach unserem Fill zu unseren Gunsten (positiv = gut).
Negativer Markout = Adverse Selection: wir werden vor allem dann gefuellt, wenn der
Preis danach gegen uns laeuft.
Grenzen: unsere Orders bewegen den abgespielten Markt nicht; IOCs treffen das Buch
ohne Verzoegerung. Fuer kleine Mengen ist das brauchbar, fuer grosse zu optimistisch.
