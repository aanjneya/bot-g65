# Folge-Aktien (47 Minuten, Verzoegerung 0.5s, halten 5s, max 10 Stueck)

Spruenge je Aktie: AMD 5, AVGO 5, CRWV 4, MU 5, NBIS 4, NVDA 7, SNDK 6, VRT 11

| Urteil | Wenn | dann | Faelle | Gewinn je Fall | Treffer | Gewinn je 30 min |
|---|---|---|---|---|---|---|
| ZU WENIG FAELLE | NVDA | SNDK | 7 | +8.10 | 100% | +36 |
| ZU WENIG FAELLE | MU | CRWV | 5 | +7.96 | 60% | +25 |
| ZU WENIG FAELLE | AVGO | NBIS | 5 | +6.60 | 80% | +21 |

## Wie schnell muss der Bot sein? (Gewinn gesamt, 5 beste Paare)
| Paar | haltezeit | 0.25s | 0.5s | 1.0s | 2.0s |
|---|---|---|---|---|---|
| NVDA -> SNDK | 2s | -21 | +22 | +48 | +34 |
| NVDA -> SNDK | 5s | +55 | +57 | +61 | +47 |
| NVDA -> SNDK | 15s | +54 | +55 | +59 | +51 |
| MU -> CRWV | 2s | +10 | +37 | -1 | -3 |
| MU -> CRWV | 5s | +31 | +40 | -2 | -3 |
| MU -> CRWV | 15s | +27 | +14 | -4 | -7 |
| AVGO -> NBIS | 2s | +60 | +50 | +25 | +4 |
| AVGO -> NBIS | 5s | +74 | +33 | +15 | +4 |
| AVGO -> NBIS | 15s | +76 | +19 | +15 | +5 |

KANDIDAT = in beiden Haelften Gewinn und deutlich. Trotzdem auf neuen Daten pruefen:
bei 56 Paaren sieht eines leicht durch Zufall gut aus. Die Sprunggrenze nutzt alle
Daten (im Live-Bot erst aus der Vergangenheit berechnen).
