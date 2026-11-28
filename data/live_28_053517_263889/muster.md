# Muster (Stand 28.11. 06:05, 30 Minuten Daten)

- HANDELBAR: in beiden Haelften da UND groesser als der Spread -> sofort handeln (IOC)
- NUR FAIR VALUE: stabil, aber kleiner als der Spread -> nur eigene Preise verschieben
- NICHT STABIL: nur in der 1. Haelfte -> ignorieren

Effekt und Netto = Preis pro Stueck. Es werden sehr viele Muster getestet,
einige wirken nur durch Zufall. Vertraut dem, was auch mit neuen Daten bleibt.

## HANDELBAR (0)

## NUR FAIR VALUE (4)
| Muster | Aktie | nach | Preis laeuft | Faelle | Effekt 2. Haelfte | Netto |
|---|---|---|---|---|---|---|
| Orderbuch stark einseitig | MU | 5s | mit dem Signal | 232 | +0.064 | -0.043 |
| Orderbuch stark einseitig | VRT | 1s | mit dem Signal | 500 | +0.010 | -0.105 |
| Orderbuch stark einseitig | SNDK | 1s | mit dem Signal | 615 | +0.008 | -0.118 |
| Orderbuch stark einseitig | NBIS | 1s | mit dem Signal | 689 | +0.006 | -0.128 |

## NICHT STABIL (5)
| Muster | Aktie | nach | Preis laeuft | Faelle | Effekt 2. Haelfte | Netto |
|---|---|---|---|---|---|---|
| Orderbuch stark einseitig | MU | 1s | mit dem Signal | 540 | +0.024 | -0.081 |
| Grosser Trade (ab 20) | MU | 1s | mit dem Signal | 118 | +0.011 | -0.097 |
| Orderbuch stark einseitig | NBIS | 5s | mit dem Signal | 270 | +0.009 | -0.127 |
| Orderbuch stark einseitig | SNDK | 5s | mit dem Signal | 254 | -0.011 | -0.137 |
| Orderbuch stark einseitig | SNDK | 15s | mit dem Signal | 104 | -0.062 | -0.188 |
