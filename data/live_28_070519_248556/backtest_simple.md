# Backtest: simple auf /home/workspace/data/live_28_070519_248556

Dauer: 18.9 min. PnL bewertet zur Mitte des Buchs.

**ABBRUCH wie auf Optibook: mehr als 25 Anfragen pro Sekunde**

| Kennzahl | Wert |
|---|---|
| PnL gesamt | -14.1 |
| 1. Haelfte / 2. Haelfte | -169.3 / +155.3 |
| Groesster Rueckgang | -284.1 |
| Fills (passiv / aggressiv) | 534 / 0 |
| Max. Summe aller Positionen | 110 |
| Max. Anfragen pro Sekunde | 26 |

## Pro Aktie

| Aktie | PnL | Fills | Endposition | Markout 1s | Markout 5s |
|---|---|---|---|---|---|
| AMD | +147.1 | 91 | 10 | -0.087 | -0.130 |
| AVGO | -90.0 | 16 | -32 | -0.312 | -0.275 |
| CRWV | +23.2 | 76 | -4 | +0.013 | +0.006 |
| MU | +6.9 | 27 | -9 | -0.044 | -0.013 |
| NBIS | +8.3 | 81 | -9 | -0.017 | -0.031 |
| NVDA | -43.2 | 132 | 12 | -0.029 | -0.017 |
| SNDK | -1.7 | 61 | -3 | -0.022 | -0.012 |
| VRT | -54.5 | 50 | 11 | -0.087 | -0.070 |

Markout = Preisbewegung nach unserem Fill zu unseren Gunsten (positiv = gut).
Negativer Markout = Adverse Selection: wir werden vor allem dann gefuellt, wenn der
Preis danach gegen uns laeuft.
Grenzen: unsere Orders bewegen den abgespielten Markt nicht; IOCs treffen das Buch
ohne Verzoegerung. Fuer kleine Mengen ist das brauchbar, fuer grosse zu optimistisch.
