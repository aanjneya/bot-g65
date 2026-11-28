# Backtest: runner:BacktestBot auf /home/workspace/data/live_28_001232_049570

Dauer: 60.0 min. PnL bewertet zur Mitte des Buchs.

| Kennzahl | Wert |
|---|---|
| PnL gesamt | -512.1 |
| 1. Haelfte / 2. Haelfte | -326.4 / -185.6 |
| Groesster Rueckgang | -513.6 |
| Fills (passiv / aggressiv) | 867 / 54 |
| Max. Summe aller Positionen | 35 |
| Max. Anfragen pro Sekunde | 17 |

## Pro Aktie

| Aktie | PnL | Fills | Endposition | Markout 1s | Markout 5s |
|---|---|---|---|---|---|
| AMD | -27.7 | 131 | 0 | -0.094 | -0.090 |
| AVGO | -243.2 | 175 | 0 | -0.225 | -0.305 |
| CRWV | -10.5 | 179 | 0 | +0.017 | -0.013 |
| MU | -36.9 | 29 | 0 | -0.260 | -0.238 |
| NBIS | -41.5 | 190 | 0 | +0.003 | +0.045 |
| NVDA | -103.6 | 68 | 0 | -0.318 | -0.300 |
| SNDK | -41.1 | 105 | 0 | -0.028 | -0.117 |
| VRT | -7.6 | 44 | 0 | -0.226 | -0.215 |

Markout = Preisbewegung nach unserem Fill zu unseren Gunsten (positiv = gut).
Negativer Markout = Adverse Selection: wir werden vor allem dann gefuellt, wenn der
Preis danach gegen uns laeuft.
Grenzen: unsere Orders bewegen den abgespielten Markt nicht; IOCs treffen das Buch
ohne Verzoegerung. Fuer kleine Mengen ist das brauchbar, fuer grosse zu optimistisch.
