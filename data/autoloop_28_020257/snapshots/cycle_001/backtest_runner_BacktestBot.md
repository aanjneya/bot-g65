# Backtest: runner:BacktestBot auf /home/workspace/data/autoloop_28_020257/snapshots/cycle_001

Dauer: 20.0 min. PnL bewertet zur Mitte des Buchs.

| Kennzahl | Wert |
|---|---|
| PnL gesamt | -61.2 |
| 1. Haelfte / 2. Haelfte | -29.4 / -31.8 |
| Groesster Rueckgang | -75.4 |
| Fills (passiv / aggressiv) | 448 / 7 |
| Max. Summe aller Positionen | 16 |
| Max. Anfragen pro Sekunde | 15 |

## Pro Aktie

| Aktie | PnL | Fills | Endposition | Markout 1s | Markout 5s |
|---|---|---|---|---|---|
| AMD | -29.5 | 13 | 0 | +0.231 | +0.238 |
| AVGO | -1.0 | 19 | 0 | -0.024 | -0.013 |
| CRWV | -2.1 | 161 | 0 | +0.010 | +0.005 |
| MU | -2.5 | 3 | 0 | -0.183 | -0.183 |
| NBIS | +13.8 | 171 | 0 | +0.029 | +0.031 |
| NVDA | -10.0 | 10 | 0 | -0.215 | -0.200 |
| SNDK | -1.9 | 63 | 0 | +0.005 | -0.037 |
| VRT | -28.0 | 15 | 0 | -0.367 | -0.347 |

Markout = Preisbewegung nach unserem Fill zu unseren Gunsten (positiv = gut).
Negativer Markout = Adverse Selection: wir werden vor allem dann gefuellt, wenn der
Preis danach gegen uns laeuft.
Grenzen: unsere Orders bewegen den abgespielten Markt nicht; IOCs treffen das Buch
ohne Verzoegerung. Fuer kleine Mengen ist das brauchbar, fuer grosse zu optimistisch.
