# Folge-Aktien (19 Minuten, Verzoegerung 0.5s, halten 5s, max 10 Stueck)

Spruenge je Aktie: AMD 4, AVGO 6, CRWV 0, MU 1, NBIS 3, NVDA 1, SNDK 1, VRT 3

| Urteil | Wenn | dann | Faelle | Gewinn je Fall | Treffer | Gewinn je 30 min |
|---|---|---|---|---|---|---|
| ZU WENIG FAELLE | AVGO | NBIS | 6 | +12.83 | 50% | +123 |
| ZU WENIG FAELLE | NVDA | SNDK | 1 | +8.50 | 100% | +14 |
| ZU WENIG FAELLE | MU | CRWV | 1 | +2.00 | 100% | +3 |

## Wie schnell muss der Bot sein? (Gewinn gesamt, 5 beste Paare)
| Paar | haltezeit | 0.25s | 0.5s | 1.0s | 2.0s |
|---|---|---|---|---|---|
| AVGO -> NBIS | 2s | +63 | +70 | +19 | +6 |
| AVGO -> NBIS | 5s | +74 | +77 | +21 | +12 |
| AVGO -> NBIS | 15s | +107 | +111 | +46 | +18 |
| NVDA -> SNDK | 2s | -0 | -0 | -0 | +15 |
| NVDA -> SNDK | 5s | +9 | +9 | +9 | +8 |
| NVDA -> SNDK | 15s | +8 | +8 | +8 | +7 |
| MU -> CRWV | 2s | -0 | +0 | +0 | +0 |
| MU -> CRWV | 5s | +1 | +2 | +2 | +2 |
| MU -> CRWV | 15s | +5 | +3 | +4 | +4 |

KANDIDAT = in beiden Haelften Gewinn und deutlich. Trotzdem auf neuen Daten pruefen:
bei 56 Paaren sieht eines leicht durch Zufall gut aus. Die Sprunggrenze nutzt alle
Daten (im Live-Bot erst aus der Vergangenheit berechnen).
