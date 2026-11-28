# Backtest: runner:BacktestBot auf /home/workspace/data/autoloop_28_023335/snapshots/cycle_002

Dauer: 12.1 min. PnL bewertet zur Mitte des Buchs.

| Kennzahl | Wert |
|---|---|
| PnL gesamt | -21.2 |
| 1. Haelfte / 2. Haelfte | -10.2 / -11.0 |
| Groesster Rueckgang | -21.6 |
| Fills (passiv / aggressiv) | 353 / 10 |
| Max. Summe aller Positionen | 3 |
| Max. Anfragen pro Sekunde | 17 |

## Pro Aktie

| Aktie | PnL | Fills | Endposition | Markout 1s | Markout 5s |
|---|---|---|---|---|---|
| AMD | -0.0 | 8 | 0 | +0.006 | +0.025 |
| AVGO | -3.2 | 22 | 0 | -0.141 | -0.059 |
| CRWV | +0.7 | 250 | 0 | -0.007 | -0.016 |
| MU | -5.3 | 6 | 0 | -0.883 | -0.883 |
| NBIS | -0.2 | 46 | 0 | -0.000 | +0.012 |
| NVDA | -8.1 | 6 | 0 | -1.342 | -1.350 |
| SNDK | -2.6 | 17 | -1 | -0.168 | -0.172 |
| VRT | -2.4 | 8 | 0 | -0.312 | -0.338 |

Markout = Preisbewegung nach unserem Fill zu unseren Gunsten (positiv = gut).
Negativer Markout = Adverse Selection: wir werden vor allem dann gefuellt, wenn der
Preis danach gegen uns laeuft.
Grenzen: unsere Orders bewegen den abgespielten Markt nicht; IOCs treffen das Buch
ohne Verzoegerung. Fuer kleine Mengen ist das brauchbar, fuer grosse zu optimistisch.
