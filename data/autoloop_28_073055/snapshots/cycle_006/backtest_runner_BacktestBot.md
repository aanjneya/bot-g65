# Backtest: runner:BacktestBot auf /home/workspace/data/autoloop_28_073055/snapshots/cycle_006

Dauer: 26.5 min. PnL bewertet zur Mitte des Buchs.

| Kennzahl | Wert |
|---|---|
| PnL gesamt | -1.5 |
| 1. Haelfte / 2. Haelfte | -3.9 / +2.4 |
| Groesster Rueckgang | -4.3 |
| Fills (passiv / aggressiv) | 64 / 1 |
| Max. Summe aller Positionen | 2 |
| Max. Anfragen pro Sekunde | 12 |

## Pro Aktie

| Aktie | PnL | Fills | Endposition | Markout 1s | Markout 5s |
|---|---|---|---|---|---|
| AMD | -1.2 | 8 | 0 | -0.013 | -0.037 |
| AVGO | +0.0 | 0 | 0 | - | - |
| CRWV | -0.7 | 32 | 0 | +0.008 | -0.003 |
| MU | +0.0 | 0 | 0 | - | - |
| NBIS | -2.5 | 19 | -1 | -0.063 | -0.136 |
| NVDA | +3.1 | 2 | 0 | +1.550 | +1.500 |
| SNDK | -0.1 | 4 | 0 | -0.025 | -0.025 |
| VRT | +0.0 | 0 | 0 | - | - |

Markout = Preisbewegung nach unserem Fill zu unseren Gunsten (positiv = gut).
Negativer Markout = Adverse Selection: wir werden vor allem dann gefuellt, wenn der
Preis danach gegen uns laeuft.
Grenzen: unsere Orders bewegen den abgespielten Markt nicht; IOCs treffen das Buch
ohne Verzoegerung. Fuer kleine Mengen ist das brauchbar, fuer grosse zu optimistisch.
