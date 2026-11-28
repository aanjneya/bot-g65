# Backtest: runner:BacktestBot auf /home/workspace/data/autoloop_28_073055/snapshots/cycle_003

Dauer: 39.6 min. PnL bewertet zur Mitte des Buchs.

| Kennzahl | Wert |
|---|---|
| PnL gesamt | -18.7 |
| 1. Haelfte / 2. Haelfte | -16.9 / -1.8 |
| Groesster Rueckgang | -19.9 |
| Fills (passiv / aggressiv) | 478 / 8 |
| Max. Summe aller Positionen | 3 |
| Max. Anfragen pro Sekunde | 14 |

## Pro Aktie

| Aktie | PnL | Fills | Endposition | Markout 1s | Markout 5s |
|---|---|---|---|---|---|
| AMD | -10.6 | 18 | 0 | -0.589 | -0.583 |
| AVGO | -10.2 | 20 | 0 | -0.510 | -0.525 |
| CRWV | -0.4 | 152 | 0 | +0.008 | +0.010 |
| MU | +0.0 | 0 | 0 | - | - |
| NBIS | +5.3 | 290 | 0 | +0.019 | +0.016 |
| NVDA | -2.9 | 2 | 0 | -1.450 | -1.450 |
| SNDK | +0.1 | 4 | 0 | +0.025 | +0.025 |
| VRT | +0.0 | 0 | 0 | - | - |

Markout = Preisbewegung nach unserem Fill zu unseren Gunsten (positiv = gut).
Negativer Markout = Adverse Selection: wir werden vor allem dann gefuellt, wenn der
Preis danach gegen uns laeuft.
Grenzen: unsere Orders bewegen den abgespielten Markt nicht; IOCs treffen das Buch
ohne Verzoegerung. Fuer kleine Mengen ist das brauchbar, fuer grosse zu optimistisch.
