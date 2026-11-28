# Backtest: runner:BacktestBot auf /Users/aanjneya/Programming/Projects/bot-g65/data/autoloop_28_020622/snapshots/cycle_002

Dauer: 0.3 min. PnL bewertet zur Mitte des Buchs.

| Kennzahl | Wert |
|---|---|
| PnL gesamt | -7.7 |
| 1. Haelfte / 2. Haelfte | +10.2 / -18.0 |
| Groesster Rueckgang | -28.1 |
| Fills (passiv / aggressiv) | 59 / 2 |
| Max. Summe aller Positionen | 39 |
| Max. Anfragen pro Sekunde | 16 |

## Pro Aktie

| Aktie | PnL | Fills | Endposition | Markout 1s | Markout 5s |
|---|---|---|---|---|---|
| AMD | +2.3 | 12 | 0 | +0.191 | -0.150 |
| AVGO | -8.4 | 7 | 0 | -0.143 | -0.330 |
| CRWV | +4.9 | 7 | 4 | +0.364 | +0.100 |
| MU | -7.5 | 6 | 0 | -0.050 | -0.440 |
| NBIS | +3.2 | 9 | 5 | +0.039 | +0.025 |
| NVDA | -4.5 | 5 | -5 | -0.337 | -0.400 |
| SNDK | +2.4 | 11 | 3 | +0.114 | +0.239 |
| VRT | -0.5 | 4 | 0 | -0.125 | -0.287 |

Markout = Preisbewegung nach unserem Fill zu unseren Gunsten (positiv = gut).
Negativer Markout = Adverse Selection: wir werden vor allem dann gefuellt, wenn der
Preis danach gegen uns laeuft.
Grenzen: unsere Orders bewegen den abgespielten Markt nicht; IOCs treffen das Buch
ohne Verzoegerung. Fuer kleine Mengen ist das brauchbar, fuer grosse zu optimistisch.
