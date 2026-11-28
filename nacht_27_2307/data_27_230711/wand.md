# Wand-Analyse (data_27_230711)

Wand = Stufe mit mind. 400 Stueck, hoechstens 3 Ticks vom besten Preis.
Gewinn je Stueck. 'schlecht' = Ausstieg sofort an den Bid/Ask (zahlt Spread),
'mid' = Wert zur Mitte (passiver Ausstieg waere dazwischen).

## 1. Gibt es Waende und halten sie?

| Aktie | Seite | Zeit mit Wand | Wand-Dauer nahe Preis (Median s) | Preis erreicht Wand: haelt | bricht |
|---|---|---|---|---|---|
| AMD | Kauf | 99% | 2 | 23 | 4 |
| AMD | Verkauf | 100% | 2 | 57 | 15 |
| AVGO | Kauf | 94% | 1 | 36 | 9 |
| AVGO | Verkauf | 98% | 1 | 62 | 17 |
| CRWV | Kauf | 0% | - | 0 | 0 |
| CRWV | Verkauf | 77% | 3 | 37 | 13 |
| MU | Kauf | 100% | 3 | 109 | 9 |
| MU | Verkauf | 100% | 3 | 126 | 15 |
| NBIS | Kauf | 54% | 3 | 14 | 8 |
| NBIS | Verkauf | 41% | 2 | 25 | 11 |
| NVDA | Kauf | 100% | 2 | 78 | 16 |
| NVDA | Verkauf | 100% | 2 | 77 | 7 |
| SNDK | Kauf | 93% | 4 | 0 | 3 |
| SNDK | Verkauf | 100% | 5 | 76 | 0 |
| VRT | Kauf | 100% | 3 | 119 | 14 |
| VRT | Verkauf | 100% | 3 | 127 | 20 |

## 2. Was haette Penny Jumping verdient?

| Aktie | Seite | halten | Fills | Gewinn/Stueck schlecht | mid | Scratch-Anteil | Summe mid (10 Stueck) |
|---|---|---|---|---|---|---|---|
| AMD | Kauf | 1s | 112 | -0.024 | +0.038 | 29% | +43.0 |
| AMD | Kauf | 5s | 87 | -0.015 | +0.023 | 56% | +20.0 |
| AMD | Kauf | 15s | 65 | +0.017 | +0.040 | 66% | +26.0 |
| AMD | Verkauf | 1s | 157 | -0.074 | -0.018 | 34% | -27.5 |
| AMD | Verkauf | 5s | 106 | -0.078 | -0.047 | 59% | -49.5 |
| AMD | Verkauf | 15s | 81 | -0.069 | -0.055 | 80% | -44.5 |
| AVGO | Kauf | 1s | 75 | -0.089 | -0.012 | 31% | -9.0 |
| AVGO | Kauf | 5s | 59 | -0.083 | -0.051 | 64% | -30.0 |
| AVGO | Kauf | 15s | 48 | -0.115 | -0.101 | 81% | -48.5 |
| AVGO | Verkauf | 1s | 107 | -0.230 | -0.157 | 29% | -168.5 |
| AVGO | Verkauf | 5s | 80 | -0.263 | -0.222 | 56% | -178.0 |
| AVGO | Verkauf | 15s | 60 | -0.307 | -0.283 | 68% | -170.0 |
| CRWV | Verkauf | 1s | 81 | +0.009 | +0.065 | 17% | +53.0 |
| CRWV | Verkauf | 5s | 52 | +0.050 | +0.097 | 33% | +50.5 |
| CRWV | Verkauf | 15s | 38 | +0.082 | +0.113 | 55% | +43.0 |
| MU | Kauf | 1s | 339 | -0.076 | -0.001 | 18% | -3.5 |
| MU | Kauf | 5s | 159 | -0.057 | -0.003 | 36% | -5.5 |
| MU | Kauf | 15s | 90 | -0.041 | -0.002 | 51% | -2.0 |
| MU | Verkauf | 1s | 304 | -0.074 | -0.007 | 24% | -20.0 |
| MU | Verkauf | 5s | 171 | -0.068 | -0.027 | 49% | -45.5 |
| MU | Verkauf | 15s | 98 | -0.064 | -0.036 | 66% | -35.5 |
| NBIS | Kauf | 1s | 29 | +0.059 | +0.162 | 21% | +47.0 |
| NBIS | Kauf | 5s | 22 | +0.132 | +0.175 | 41% | +38.5 |
| NBIS | Kauf | 15s | 16 | +0.238 | +0.278 | 50% | +44.5 |
| NBIS | Verkauf | 1s | 42 | -0.186 | -0.120 | 31% | -50.5 |
| NBIS | Verkauf | 5s | 29 | -0.252 | -0.200 | 45% | -58.0 |
| NBIS | Verkauf | 15s | 20 | -0.320 | -0.298 | 75% | -59.5 |
| NVDA | Kauf | 1s | 181 | -0.063 | -0.003 | 29% | -5.5 |
| NVDA | Kauf | 5s | 118 | -0.053 | -0.018 | 56% | -21.0 |
| NVDA | Kauf | 15s | 79 | -0.061 | -0.037 | 67% | -29.5 |
| NVDA | Verkauf | 1s | 185 | -0.025 | +0.036 | 24% | +67.0 |
| NVDA | Verkauf | 5s | 125 | +0.026 | +0.065 | 51% | +81.5 |
| NVDA | Verkauf | 15s | 80 | +0.074 | +0.100 | 68% | +80.0 |
| SNDK | Kauf | 1s | 4 | -0.450 | -0.350 | 25% | -14.0 |
| SNDK | Kauf | 5s | 2 | -0.850 | -0.800 | 50% | -16.0 |
| SNDK | Kauf | 15s | 2 | -0.850 | -0.800 | 50% | -16.0 |
| SNDK | Verkauf | 1s | 137 | -0.045 | +0.046 | 9% | +63.5 |
| SNDK | Verkauf | 5s | 74 | +0.004 | +0.067 | 32% | +49.5 |
| SNDK | Verkauf | 15s | 50 | +0.040 | +0.095 | 42% | +47.5 |
| VRT | Kauf | 1s | 275 | -0.059 | +0.005 | 19% | +14.5 |
| VRT | Kauf | 5s | 155 | -0.032 | +0.015 | 35% | +24.0 |
| VRT | Kauf | 15s | 93 | +0.003 | +0.034 | 57% | +31.5 |
| VRT | Verkauf | 1s | 290 | -0.082 | -0.016 | 18% | -47.5 |
| VRT | Verkauf | 5s | 159 | -0.076 | -0.033 | 41% | -53.0 |
| VRT | Verkauf | 15s | 95 | -0.093 | -0.066 | 63% | -62.5 |

## 3. Gesamt

| halten | Fills | Summe schlecht | Summe mid |
|---|---|---|---|
| 1s | 2318 | -1633.0 | -58.0 |
| 5s | 1398 | -794.0 | -192.5 |
| 15s | 915 | -450.0 | -195.5 |

## 4. Mit Filter (halten 5s, Summen fuer 10 Stueck)

Ein Filter ist nur echt, wenn BEIDE Haelften besser sind als ohne Filter.

| Filter | Fills | Summe schlecht | Summe mid | mid 1. Haelfte | mid 2. Haelfte | mid je Fill |
|---|---|---|---|---|---|---|
| ohne Filter | 1398 | -794.0 | -192.5 | -131.5 | -61.0 | -0.14 |
| Microprice | 1115 | -576.0 | -116.0 | -104.0 | -12.0 | -0.10 |
| OFI | 1001 | -415.0 | -13.0 | -57.0 | +44.0 | -0.01 |
| Leader | 1364 | -762.0 | -172.5 | -112.5 | -60.0 | -0.13 |
| Trend | 1117 | -574.0 | -112.0 | -44.0 | -68.0 | -0.10 |
| Micro+OFI | 829 | -311.0 | +18.0 | -17.0 | +35.0 | +0.02 |
| alle 4 | 780 | -311.0 | -4.0 | -35.5 | +31.5 | -0.01 |

### Bester Filter pro Aktie: Micro+OFI (Summe mid / Fills)

| Aktie | AMD | AVGO | CRWV | MU | NBIS | NVDA | SNDK | VRT |
|---|---|---|---|---|---|---|---|---|
| Ergebnis | +6 / 92 | -15 / 66 | +21 / 25 | -68 / 235 | +75 / 23 | +26 / 137 | +36 / 47 | -63 / 204 |

Lesehilfe: Ist 'mid' klar positiv und 'Scratch-Anteil' niedrig, lohnt sich die
Strategie. Ist schon 'schlecht' positiv, ist sie sehr stark.
Fills sind geschaetzt (wir stehen hinter der Menge, die schon auf unserem Preis lag).
