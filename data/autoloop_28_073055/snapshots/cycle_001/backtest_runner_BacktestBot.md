# Backtest: runner:BacktestBot auf /home/workspace/data/autoloop_28_073055/snapshots/cycle_001

Dauer: 11.8 min. PnL bewertet zur Mitte des Buchs.

| Kennzahl | Wert |
|---|---|
| PnL gesamt | -27.6 |
| 1. Haelfte / 2. Haelfte | -19.9 / -7.6 |
| Groesster Rueckgang | -27.8 |
| Fills (passiv / aggressiv) | 191 / 9 |
| Max. Summe aller Positionen | 2 |
| Max. Anfragen pro Sekunde | 17 |

## Pro Aktie

| Aktie | PnL | Fills | Endposition | Markout 1s | Markout 5s |
|---|---|---|---|---|---|
| AMD | -6.7 | 16 | 0 | -0.437 | -0.431 |
| AVGO | -4.9 | 12 | 0 | -0.425 | -0.408 |
| CRWV | -0.4 | 78 | 0 | +0.006 | -0.011 |
| MU | -1.3 | 6 | 0 | -0.217 | -0.233 |
| NBIS | +0.1 | 66 | 0 | +0.002 | -0.004 |
| NVDA | -9.1 | 8 | 0 | -1.144 | -1.125 |
| SNDK | -3.5 | 10 | 0 | -0.345 | -0.350 |
| VRT | -1.8 | 4 | 0 | -0.450 | -0.400 |

Markout = Preisbewegung nach unserem Fill zu unseren Gunsten (positiv = gut).
Negativer Markout = Adverse Selection: wir werden vor allem dann gefuellt, wenn der
Preis danach gegen uns laeuft.
Grenzen: unsere Orders bewegen den abgespielten Markt nicht; IOCs treffen das Buch
ohne Verzoegerung. Fuer kleine Mengen ist das brauchbar, fuer grosse zu optimistisch.
