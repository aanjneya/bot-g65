# Backtest: simple auf /home/workspace/data/autoloop_28_023335/live_005_043516

Dauer: 60.0 min. PnL bewertet zur Mitte des Buchs.

**ABBRUCH wie auf Optibook: mehr als 25 Anfragen pro Sekunde**

| Kennzahl | Wert |
|---|---|
| PnL gesamt | -3.2 |
| 1. Haelfte / 2. Haelfte | -3.2 / +0.0 |
| Groesster Rueckgang | -3.9 |
| Fills (passiv / aggressiv) | 29 / 0 |
| Max. Summe aller Positionen | 46 |
| Max. Anfragen pro Sekunde | 26 |

## Pro Aktie

| Aktie | PnL | Fills | Endposition | Markout 1s | Markout 5s |
|---|---|---|---|---|---|
| AMD | +1.2 | 9 | -5 | +0.050 | - |
| AVGO | -2.5 | 7 | 0 | -0.021 | -0.200 |
| CRWV | -1.0 | 2 | -10 | -0.050 | -0.150 |
| MU | -0.5 | 2 | -10 | +0.000 | -0.050 |
| NBIS | +0.5 | 3 | 1 | +0.017 | - |
| NVDA | -0.2 | 3 | -4 | -0.000 | - |
| SNDK | -0.2 | 1 | -5 | +0.000 | - |
| VRT | -0.5 | 2 | -10 | -0.100 | - |

Markout = Preisbewegung nach unserem Fill zu unseren Gunsten (positiv = gut).
Negativer Markout = Adverse Selection: wir werden vor allem dann gefuellt, wenn der
Preis danach gegen uns laeuft.
Grenzen: unsere Orders bewegen den abgespielten Markt nicht; IOCs treffen das Buch
ohne Verzoegerung. Fuer kleine Mengen ist das brauchbar, fuer grosse zu optimistisch.
