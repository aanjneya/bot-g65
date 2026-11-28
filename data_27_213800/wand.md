# Wand-Analyse (data_27_213800)

Wand = Stufe mit mind. 400 Stueck, hoechstens 3 Ticks vom besten Preis.
Gewinn je Stueck. 'schlecht' = Ausstieg sofort an den Bid/Ask (zahlt Spread),
'mid' = Wert zur Mitte (passiver Ausstieg waere dazwischen).

## 1. Gibt es Waende und halten sie?

| Aktie | Seite | Zeit mit Wand | Wand-Dauer nahe Preis (Median s) | Preis erreicht Wand: haelt | bricht |
|---|---|---|---|---|---|
| AMD | Kauf | 100% | 2 | 68 | 15 |
| AMD | Verkauf | 99% | 2 | 61 | 13 |
| AVGO | Kauf | 95% | 1 | 44 | 33 |
| AVGO | Verkauf | 92% | 1 | 48 | 21 |
| CRWV | Kauf | 12% | 2 | 14 | 0 |
| CRWV | Verkauf | 43% | 3 | 37 | 7 |
| MU | Kauf | 100% | 3 | 83 | 10 |
| MU | Verkauf | 100% | 3 | 86 | 11 |
| NBIS | Kauf | 37% | 3 | 17 | 2 |
| NBIS | Verkauf | 74% | 4 | 21 | 5 |
| NVDA | Kauf | 100% | 2 | 92 | 24 |
| NVDA | Verkauf | 100% | 2 | 88 | 27 |
| SNDK | Kauf | 98% | 5 | 42 | 1 |
| SNDK | Verkauf | 96% | 5 | 42 | 1 |
| VRT | Kauf | 100% | 3 | 100 | 9 |
| VRT | Verkauf | 100% | 3 | 90 | 17 |

## 2. Was haette Penny Jumping verdient?

| Aktie | Seite | halten | Fills | Gewinn/Stueck schlecht | mid | Scratch-Anteil | Summe mid (10 Stueck) |
|---|---|---|---|---|---|---|---|
| AMD | Kauf | 1s | 81 | -0.191 | -0.120 | 37% | -97.5 |
| AMD | Kauf | 5s | 66 | -0.183 | -0.123 | 48% | -81.5 |
| AMD | Kauf | 15s | 56 | -0.171 | -0.138 | 68% | -77.5 |
| AMD | Verkauf | 1s | 77 | -0.013 | +0.070 | 31% | +54.0 |
| AMD | Verkauf | 5s | 61 | +0.008 | +0.060 | 52% | +36.5 |
| AMD | Verkauf | 15s | 49 | +0.002 | +0.021 | 80% | +10.5 |
| AVGO | Kauf | 1s | 68 | -0.065 | +0.012 | 41% | +8.0 |
| AVGO | Kauf | 5s | 58 | -0.045 | -0.009 | 71% | -5.5 |
| AVGO | Kauf | 15s | 50 | -0.024 | -0.003 | 82% | -1.5 |
| AVGO | Verkauf | 1s | 56 | -0.073 | +0.011 | 30% | +6.0 |
| AVGO | Verkauf | 5s | 46 | -0.002 | +0.052 | 52% | +24.0 |
| AVGO | Verkauf | 15s | 39 | +0.038 | +0.082 | 64% | +32.0 |
| CRWV | Kauf | 1s | 9 | +0.456 | +0.817 | 11% | +73.5 |
| CRWV | Kauf | 5s | 6 | +1.050 | +1.250 | 17% | +75.0 |
| CRWV | Kauf | 15s | 5 | +1.260 | +1.350 | 40% | +67.5 |
| CRWV | Verkauf | 1s | 13 | +0.054 | +0.223 | 8% | +29.0 |
| CRWV | Verkauf | 5s | 11 | +0.073 | +0.227 | 27% | +25.0 |
| CRWV | Verkauf | 15s | 11 | +0.236 | +0.332 | 27% | +36.5 |
| MU | Kauf | 1s | 90 | -0.110 | -0.036 | 27% | -32.5 |
| MU | Kauf | 5s | 76 | -0.095 | -0.040 | 46% | -30.5 |
| MU | Kauf | 15s | 58 | -0.067 | -0.026 | 57% | -15.0 |
| MU | Verkauf | 1s | 110 | -0.177 | -0.095 | 16% | -104.0 |
| MU | Verkauf | 5s | 82 | -0.185 | -0.123 | 32% | -100.5 |
| MU | Verkauf | 15s | 55 | -0.236 | -0.190 | 51% | -104.5 |
| NBIS | Kauf | 1s | 21 | +0.033 | +0.195 | 10% | +41.0 |
| NBIS | Kauf | 5s | 18 | +0.122 | +0.269 | 17% | +48.5 |
| NBIS | Kauf | 15s | 14 | +0.186 | +0.289 | 36% | +40.5 |
| NBIS | Verkauf | 1s | 13 | +0.031 | +0.142 | 8% | +18.5 |
| NBIS | Verkauf | 5s | 11 | +0.036 | +0.100 | 36% | +11.0 |
| NBIS | Verkauf | 15s | 11 | +0.064 | +0.114 | 55% | +12.5 |
| NVDA | Kauf | 1s | 83 | -0.112 | -0.039 | 34% | -32.0 |
| NVDA | Kauf | 5s | 69 | -0.088 | -0.047 | 58% | -32.5 |
| NVDA | Kauf | 15s | 54 | -0.096 | -0.056 | 67% | -30.0 |
| NVDA | Verkauf | 1s | 87 | -0.070 | +0.012 | 26% | +10.5 |
| NVDA | Verkauf | 5s | 69 | +0.003 | +0.064 | 48% | +44.0 |
| NVDA | Verkauf | 15s | 58 | +0.038 | +0.074 | 67% | +43.0 |
| SNDK | Kauf | 1s | 45 | -0.038 | +0.074 | 7% | +33.5 |
| SNDK | Kauf | 5s | 36 | -0.039 | +0.051 | 22% | +18.5 |
| SNDK | Kauf | 15s | 27 | +0.000 | +0.072 | 33% | +19.5 |
| SNDK | Verkauf | 1s | 31 | -0.026 | +0.097 | 10% | +30.0 |
| SNDK | Verkauf | 5s | 25 | +0.012 | +0.130 | 12% | +32.5 |
| SNDK | Verkauf | 15s | 18 | +0.050 | +0.117 | 39% | +21.0 |
| VRT | Kauf | 1s | 107 | -0.150 | -0.077 | 21% | -82.0 |
| VRT | Kauf | 5s | 87 | -0.141 | -0.094 | 41% | -81.5 |
| VRT | Kauf | 15s | 63 | -0.159 | -0.118 | 54% | -74.5 |
| VRT | Verkauf | 1s | 109 | -0.099 | -0.029 | 28% | -31.5 |
| VRT | Verkauf | 5s | 82 | -0.087 | -0.035 | 46% | -28.5 |
| VRT | Verkauf | 15s | 60 | -0.082 | -0.044 | 62% | -26.5 |

## 3. Gesamt

| halten | Fills | Summe schlecht | Summe mid |
|---|---|---|---|
| 1s | 1000 | -933.0 | -75.5 |
| 5s | 803 | -534.0 | -45.5 |
| 15s | 628 | -309.0 | -46.5 |

## 4. Mit Filter (halten 5s, Summen fuer 10 Stueck)

Ein Filter ist nur echt, wenn BEIDE Haelften besser sind als ohne Filter.

| Filter | Fills | Summe schlecht | Summe mid | mid 1. Haelfte | mid 2. Haelfte | mid je Fill |
|---|---|---|---|---|---|---|
| ohne Filter | 803 | -534.0 | -45.5 | +16.5 | -62.0 | -0.06 |
| Microprice | 721 | -364.0 | +71.0 | +75.5 | -4.5 | +0.10 |
| OFI | 613 | -410.0 | -54.5 | +39.0 | -93.5 | -0.09 |
| Leader | 787 | -531.0 | -57.5 | +10.0 | -67.5 | -0.07 |
| Trend | 647 | -429.0 | -42.5 | +37.0 | -79.5 | -0.07 |
| Micro+OFI | 558 | -311.0 | +13.0 | +52.5 | -39.5 | +0.02 |
| alle 4 | 545 | -300.0 | +15.0 | +46.0 | -31.0 | +0.03 |

### Bester Filter pro Aktie: Microprice (Summe mid / Fills)

| Aktie | AMD | AVGO | CRWV | MU | NBIS | NVDA | SNDK | VRT |
|---|---|---|---|---|---|---|---|---|
| Ergebnis | -61 / 113 | +62 / 92 | +96 / 16 | -111 / 138 | +48 / 23 | +29 / 124 | +47 / 54 | -39 / 161 |

Lesehilfe: Ist 'mid' klar positiv und 'Scratch-Anteil' niedrig, lohnt sich die
Strategie. Ist schon 'schlecht' positiv, ist sie sehr stark.
Fills sind geschaetzt (wir stehen hinter der Menge, die schon auf unserem Preis lag).
