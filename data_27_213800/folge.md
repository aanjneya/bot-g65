# Folge-Aktien (22 Minuten, Verzoegerung 0.5s, halten 5s, max 10 Stueck)

Spruenge je Aktie: AMD 7, AVGO 5, CRWV 5, MU 2, NBIS 1, NVDA 1, SNDK 1, VRT 3

| Urteil | Wenn | dann | Faelle | Gewinn je Fall | Treffer | Gewinn je 30 min |
|---|---|---|---|---|---|---|
| ZU WENIG FAELLE | MU | CRWV | 2 | +32.25 | 100% | +87 |
| ZU WENIG FAELLE | AVGO | NBIS | 5 | +3.00 | 80% | +20 |
| ZU WENIG FAELLE | NVDA | SNDK | 1 | +14.00 | 100% | +19 |

## Wie schnell muss der Bot sein? (Gewinn gesamt, 5 beste Paare)
| Paar | haltezeit | 0.25s | 0.5s | 1.0s | 2.0s |
|---|---|---|---|---|---|
| MU -> CRWV | 2s | +47 | +48 | +28 | +4 |
| MU -> CRWV | 5s | +64 | +64 | +45 | +17 |
| MU -> CRWV | 15s | +75 | +75 | +30 | +15 |
| AVGO -> NBIS | 2s | +4 | +3 | -0 | -4 |
| AVGO -> NBIS | 5s | +9 | +15 | +4 | +5 |
| AVGO -> NBIS | 15s | +21 | +20 | +4 | +6 |
| NVDA -> SNDK | 2s | -1 | -1 | +12 | +12 |
| NVDA -> SNDK | 5s | +14 | +14 | +15 | +14 |
| NVDA -> SNDK | 15s | +15 | +15 | +15 | +14 |

KANDIDAT = in beiden Haelften Gewinn und deutlich. Trotzdem auf neuen Daten pruefen:
bei 56 Paaren sieht eines leicht durch Zufall gut aus. Die Sprunggrenze nutzt alle
Daten (im Live-Bot erst aus der Vergangenheit berechnen).
