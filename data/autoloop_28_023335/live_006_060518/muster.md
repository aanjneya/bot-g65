# Muster (Stand 28.11. 07:05, 60 Minuten Daten)

- HANDELBAR: in beiden Haelften da UND groesser als der Spread -> sofort handeln (IOC)
- NUR FAIR VALUE: stabil, aber kleiner als der Spread -> nur eigene Preise verschieben
- NICHT STABIL: nur in der 1. Haelfte -> ignorieren

Effekt und Netto = Preis pro Stueck. Es werden sehr viele Muster getestet,
einige wirken nur durch Zufall. Vertraut dem, was auch mit neuen Daten bleibt.

## HANDELBAR (0)

## NUR FAIR VALUE (7)
| Muster | Aktie | nach | Preis laeuft | Faelle | Effekt 2. Haelfte | Netto |
|---|---|---|---|---|---|---|
| Orderbuch stark einseitig | AMD | 1s | mit dem Signal | 1223 | +0.024 | -0.088 |
| Orderbuch stark einseitig | NBIS | 15s | mit dem Signal | 199 | +0.031 | -0.093 |
| Orderbuch stark einseitig | MU | 1s | mit dem Signal | 1106 | +0.011 | -0.096 |
| Orderbuch stark einseitig | NBIS | 5s | mit dem Signal | 441 | +0.019 | -0.102 |
| Orderbuch stark einseitig | ALLE | 1s | mit dem Signal | 8843 | +0.012 | -0.104 |
| Orderbuch stark einseitig | NBIS | 1s | mit dem Signal | 1008 | +0.006 | -0.116 |
| Orderbuch stark einseitig | CRWV | 1s | mit dem Signal | 800 | +0.011 | -0.124 |

## NICHT STABIL (3)
| Muster | Aktie | nach | Preis laeuft | Faelle | Effekt 2. Haelfte | Netto |
|---|---|---|---|---|---|---|
| Mitte der grossen Orders weicht ab | AVGO | 5s | mit dem Signal | 99 | +0.043 | -0.091 |
| Grosser Trade (ab 20) | MU | 5s | gegen das Signal | 150 | +0.002 | -0.111 |
| Mitte der grossen Orders weicht ab | AVGO | 1s | mit dem Signal | 145 | +0.013 | -0.123 |
