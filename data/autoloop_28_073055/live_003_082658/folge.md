# Folge-Aktien (28 Minuten, Verzoegerung 0.5s, halten 5s, max 10 Stueck)

Spruenge je Aktie: AMD 2, AVGO 7, CRWV 7, MU 7, NBIS 5, NVDA 3, SNDK 1, VRT 2

| Urteil | Wenn | dann | Faelle | Gewinn je Fall | Treffer | Gewinn je 30 min |
|---|---|---|---|---|---|---|
| ZU WENIG FAELLE | AVGO | NBIS | 7 | +6.71 | 86% | +51 |
| ZU WENIG FAELLE | MU | CRWV | 7 | +1.01 | 57% | +8 |
| ZU WENIG FAELLE | NVDA | SNDK | 3 | +1.67 | 67% | +5 |

## Wie schnell muss der Bot sein? (Gewinn gesamt, 5 beste Paare)
| Paar | haltezeit | 0.25s | 0.5s | 1.0s | 2.0s |
|---|---|---|---|---|---|
| AVGO -> NBIS | 2s | +58 | +55 | +9 | -9 |
| AVGO -> NBIS | 5s | +52 | +47 | +6 | -13 |
| AVGO -> NBIS | 15s | +48 | +45 | +5 | -14 |
| MU -> CRWV | 2s | +4 | +7 | -7 | -19 |
| MU -> CRWV | 5s | +9 | +7 | -16 | -36 |
| MU -> CRWV | 15s | +1 | +1 | -22 | -40 |
| NVDA -> SNDK | 2s | -5 | -5 | -3 | +7 |
| NVDA -> SNDK | 5s | +5 | +5 | +7 | +7 |
| NVDA -> SNDK | 15s | +7 | +7 | +9 | +10 |

KANDIDAT = in beiden Haelften Gewinn und deutlich. Trotzdem auf neuen Daten pruefen:
bei 56 Paaren sieht eines leicht durch Zufall gut aus. Die Sprunggrenze nutzt alle
Daten (im Live-Bot erst aus der Vergangenheit berechnen).
