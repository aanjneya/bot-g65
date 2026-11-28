# Muster (Stand 28.11. 05:04, 29 Minuten Daten)

- HANDELBAR: in beiden Haelften da UND groesser als der Spread -> sofort handeln (IOC)
- NUR FAIR VALUE: stabil, aber kleiner als der Spread -> nur eigene Preise verschieben
- NICHT STABIL: nur in der 1. Haelfte -> ignorieren

Effekt und Netto = Preis pro Stueck. Es werden sehr viele Muster getestet,
einige wirken nur durch Zufall. Vertraut dem, was auch mit neuen Daten bleibt.

## HANDELBAR (0)

## NUR FAIR VALUE (2)
| Muster | Aktie | nach | Preis laeuft | Faelle | Effekt 2. Haelfte | Netto |
|---|---|---|---|---|---|---|
| Orderbuch stark einseitig | NBIS | 5s | mit dem Signal | 249 | +0.028 | -0.081 |
| Orderbuch stark einseitig | NBIS | 1s | mit dem Signal | 659 | +0.008 | -0.101 |

## NICHT STABIL (3)
| Muster | Aktie | nach | Preis laeuft | Faelle | Effekt 2. Haelfte | Netto |
|---|---|---|---|---|---|---|
| Orderbuch stark einseitig | CRWV | 1s | mit dem Signal | 642 | +0.004 | -0.120 |
| Grosser Trade (ab 20) | ALLE | 1s | mit dem Signal | 718 | -0.006 | -0.131 |
| Orderbuch stark einseitig | AMD | 15s | gegen das Signal | 101 | -0.051 | -0.157 |
