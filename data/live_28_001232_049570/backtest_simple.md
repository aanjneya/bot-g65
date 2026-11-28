# Backtest: simple auf data/live_28_001232_049570

Dauer: 60.0 min. PnL bewertet zur Mitte des Buchs.

**ABBRUCH wie auf Optibook: mehr als 25 Anfragen pro Sekunde**

| Kennzahl | Wert |
|---|---|
| PnL gesamt | +2.3 |
| 1. Haelfte / 2. Haelfte | +2.3 / +0.0 |
| Groesster Rueckgang | -1.7 |
| Fills (passiv / aggressiv) | 41 / 0 |
| Max. Summe aller Positionen | 46 |
| Max. Anfragen pro Sekunde | 26 |

## Pro Aktie

| Aktie | PnL | Fills | Endposition | Markout 1s | Markout 5s |
|---|---|---|---|---|---|
| AMD | +1.4 | 7 | -3 | +0.057 | +0.062 |
| AVGO | -2.1 | 6 | 15 | -0.050 | -0.100 |
| CRWV | +1.0 | 5 | 0 | +0.038 | +0.033 |
| MU | +0.5 | 3 | 0 | +0.000 | +0.050 |
| NBIS | +0.8 | 4 | -5 | +0.000 | +0.025 |
| NVDA | +0.1 | 6 | -2 | +0.010 | +0.000 |
| SNDK | +0.0 | 5 | 5 | -0.010 | +0.033 |
| VRT | -0.2 | 5 | 11 | +0.037 | +0.017 |

Markout = Preisbewegung nach unserem Fill zu unseren Gunsten (positiv = gut).
Negativer Markout = Adverse Selection: wir werden vor allem dann gefuellt, wenn der
Preis danach gegen uns laeuft.
Grenzen: unsere Orders bewegen den abgespielten Markt nicht; IOCs treffen das Buch
ohne Verzoegerung. Fuer kleine Mengen ist das brauchbar, fuer grosse zu optimistisch.
