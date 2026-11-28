# Folge-Aktien (30 Minuten, Verzoegerung 0.5s, halten 5s, max 10 Stueck)

Spruenge je Aktie: AMD 4, AVGO 3, CRWV 4, MU 5, NBIS 2, NVDA 3, SNDK 2, VRT 3

| Urteil | Wenn | dann | Faelle | Gewinn je Fall | Treffer | Gewinn je 30 min |
|---|---|---|---|---|---|---|
| ZU WENIG FAELLE | NVDA | SNDK | 3 | +8.73 | 67% | +26 |
| ZU WENIG FAELLE | MU | CRWV | 5 | +3.94 | 60% | +20 |
| ZU WENIG FAELLE | AVGO | NBIS | 3 | +5.67 | 100% | +17 |

## Wie schnell muss der Bot sein? (Gewinn gesamt, 5 beste Paare)
| Paar | haltezeit | 0.25s | 0.5s | 1.0s | 2.0s |
|---|---|---|---|---|---|
| NVDA -> SNDK | 2s | -4 | -3 | +23 | +30 |
| NVDA -> SNDK | 5s | +28 | +26 | +27 | +30 |
| NVDA -> SNDK | 15s | +28 | +26 | +25 | +31 |
| MU -> CRWV | 2s | +0 | +12 | -2 | -5 |
| MU -> CRWV | 5s | +22 | +20 | -5 | -2 |
| MU -> CRWV | 15s | +32 | +30 | +3 | -4 |
| AVGO -> NBIS | 2s | +10 | +3 | +0 | -3 |
| AVGO -> NBIS | 5s | +19 | +17 | +1 | -9 |
| AVGO -> NBIS | 15s | +18 | +16 | +1 | -12 |

KANDIDAT = in beiden Haelften Gewinn und deutlich. Trotzdem auf neuen Daten pruefen:
bei 56 Paaren sieht eines leicht durch Zufall gut aus. Die Sprunggrenze nutzt alle
Daten (im Live-Bot erst aus der Vergangenheit berechnen).
