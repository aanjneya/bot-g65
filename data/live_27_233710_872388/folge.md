# Folge-Aktien (35 Minuten, Verzoegerung 0.5s, halten 5s, max 10 Stueck)

Spruenge je Aktie: AMD 8, AVGO 8, CRWV 3, MU 3, NBIS 6, NVDA 4, SNDK 3, VRT 1

| Urteil | Wenn | dann | Faelle | Gewinn je Fall | Treffer | Gewinn je 30 min |
|---|---|---|---|---|---|---|
| KANDIDAT | AVGO | NBIS | 8 | +1.41 | 62% | +10 |
| ZU WENIG FAELLE | NVDA | SNDK | 4 | +6.00 | 100% | +21 |
| ZU WENIG FAELLE | MU | CRWV | 3 | +3.20 | 100% | +8 |

## Wie schnell muss der Bot sein? (Gewinn gesamt, 5 beste Paare)
| Paar | haltezeit | 0.25s | 0.5s | 1.0s | 2.0s |
|---|---|---|---|---|---|
| AVGO -> NBIS | 2s | +20 | +6 | -4 | -6 |
| AVGO -> NBIS | 5s | +32 | +11 | -0 | -3 |
| AVGO -> NBIS | 15s | +8 | +2 | -8 | -7 |
| NVDA -> SNDK | 2s | -5 | +18 | +19 | +29 |
| NVDA -> SNDK | 5s | +34 | +24 | +25 | +38 |
| NVDA -> SNDK | 15s | +36 | +35 | +35 | +36 |
| MU -> CRWV | 2s | +9 | +5 | -8 | -5 |
| MU -> CRWV | 5s | +12 | +10 | +0 | +0 |
| MU -> CRWV | 15s | +14 | +11 | +1 | +2 |

KANDIDAT = in beiden Haelften Gewinn und deutlich. Trotzdem auf neuen Daten pruefen:
bei 56 Paaren sieht eines leicht durch Zufall gut aus. Die Sprunggrenze nutzt alle
Daten (im Live-Bot erst aus der Vergangenheit berechnen).
