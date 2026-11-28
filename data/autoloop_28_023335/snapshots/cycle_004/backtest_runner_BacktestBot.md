# Backtest: runner:BacktestBot auf /home/workspace/data/autoloop_28_023335/snapshots/cycle_004

Dauer: 28.5 min. PnL bewertet zur Mitte des Buchs.

| Kennzahl | Wert |
|---|---|
| PnL gesamt | -9.4 |
| 1. Haelfte / 2. Haelfte | -2.9 / -6.5 |
| Groesster Rueckgang | -15.1 |
| Fills (passiv / aggressiv) | 1005 / 9 |
| Max. Summe aller Positionen | 4 |
| Max. Anfragen pro Sekunde | 17 |

## Pro Aktie

| Aktie | PnL | Fills | Endposition | Markout 1s | Markout 5s |
|---|---|---|---|---|---|
| AMD | -0.4 | 12 | 0 | -0.037 | -0.004 |
| AVGO | -1.2 | 32 | 0 | -0.048 | -0.025 |
| CRWV | +3.2 | 414 | 0 | +0.008 | +0.007 |
| MU | -6.9 | 8 | 0 | -0.875 | -0.863 |
| NBIS | +9.4 | 472 | 0 | +0.016 | +0.020 |
| NVDA | -12.7 | 26 | 0 | -0.490 | -0.487 |
| SNDK | -0.7 | 44 | 0 | -0.070 | -0.017 |
| VRT | -0.1 | 6 | 0 | -0.008 | -0.008 |

Markout = Preisbewegung nach unserem Fill zu unseren Gunsten (positiv = gut).
Negativer Markout = Adverse Selection: wir werden vor allem dann gefuellt, wenn der
Preis danach gegen uns laeuft.
Grenzen: unsere Orders bewegen den abgespielten Markt nicht; IOCs treffen das Buch
ohne Verzoegerung. Fuer kleine Mengen ist das brauchbar, fuer grosse zu optimistisch.
