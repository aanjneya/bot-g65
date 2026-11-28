# Backtest: simple auf data_27_213800

Dauer: 22.2 min. PnL bewertet zur Mitte des Buchs.

**ABBRUCH wie auf Optibook: mehr als 25 Anfragen pro Sekunde**

| Kennzahl | Wert |
|---|---|
| PnL gesamt | +7.3 |
| 1. Haelfte / 2. Haelfte | +7.3 / +0.0 |
| Groesster Rueckgang | -0.8 |
| Fills (passiv / aggressiv) | 14 / 0 |
| Max. Summe aller Positionen | 30 |
| Max. Anfragen pro Sekunde | 26 |

## Pro Aktie

| Aktie | PnL | Fills | Endposition | Markout 1s | Markout 5s |
|---|---|---|---|---|---|
| AMD | +2.0 | 3 | 5 | +0.000 | +0.125 |
| AVGO | +1.5 | 2 | 0 | +0.050 | +0.050 |
| CRWV | +0.0 | 2 | -10 | +0.050 | +0.050 |
| MU | +0.0 | 0 | 0 | - | - |
| NBIS | -0.2 | 1 | -5 | +0.050 | +0.200 |
| NVDA | +2.0 | 2 | -10 | +0.150 | +0.150 |
| SNDK | +2.0 | 4 | 0 | +0.088 | +0.083 |
| VRT | +0.0 | 0 | 0 | - | - |

Markout = Preisbewegung nach unserem Fill zu unseren Gunsten (positiv = gut).
Negativer Markout = Adverse Selection: wir werden vor allem dann gefuellt, wenn der
Preis danach gegen uns laeuft.
Grenzen: unsere Orders bewegen den abgespielten Markt nicht; IOCs treffen das Buch
ohne Verzoegerung. Fuer kleine Mengen ist das brauchbar, fuer grosse zu optimistisch.
