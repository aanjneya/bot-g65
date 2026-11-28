# Backtest: wall auf data_27_230711

Dauer: 18.7 min. PnL bewertet zur Mitte des Buchs.

| Kennzahl | Wert |
|---|---|
| PnL gesamt | -87.7 |
| 1. Haelfte / 2. Haelfte | -84.6 / -3.1 |
| Groesster Rueckgang | -133.6 |
| Fills (passiv / aggressiv) | 2249 / 317 |
| Max. Summe aller Positionen | 81 |
| Max. Anfragen pro Sekunde | 15 |

## Pro Aktie

| Aktie | PnL | Fills | Endposition | Markout 1s | Markout 5s |
|---|---|---|---|---|---|
| AMD | -42.7 | 450 | -5 | -0.009 | -0.023 |
| AVGO | -56.5 | 357 | 9 | +0.019 | -0.059 |
| CRWV | +17.3 | 156 | 0 | +0.025 | +0.021 |
| MU | +21.4 | 394 | 0 | +0.017 | +0.022 |
| NBIS | -13.0 | 127 | 0 | +0.004 | -0.016 |
| NVDA | -18.7 | 571 | -10 | +0.009 | +0.005 |
| SNDK | +4.6 | 285 | -2 | +0.028 | -0.014 |
| VRT | +0.4 | 226 | 0 | +0.026 | +0.029 |

Markout = Preisbewegung nach unserem Fill zu unseren Gunsten (positiv = gut).
Negativer Markout = Adverse Selection: wir werden vor allem dann gefuellt, wenn der
Preis danach gegen uns laeuft.
Grenzen: unsere Orders bewegen den abgespielten Markt nicht; IOCs treffen das Buch
ohne Verzoegerung. Fuer kleine Mengen ist das brauchbar, fuer grosse zu optimistisch.
