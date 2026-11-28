# Folge-Aktien (60 Minuten, Verzoegerung 0.5s, halten 5s, max 10 Stueck)

Spruenge je Aktie: AMD 10, AVGO 5, CRWV 7, MU 6, NBIS 4, NVDA 10, SNDK 8, VRT 10

| Urteil | Wenn | dann | Faelle | Gewinn je Fall | Treffer | Gewinn je 30 min |
|---|---|---|---|---|---|---|
| KANDIDAT | NVDA | SNDK | 10 | +7.75 | 90% | +39 |
| ZU WENIG FAELLE | AVGO | NBIS | 5 | +4.38 | 80% | +11 |
| ZU WENIG FAELLE | MU | CRWV | 6 | +1.87 | 33% | +6 |
| ZU WENIG FAELLE | NBIS | AVGO | 4 | +0.25 | 50% | +0 |
| ZU WENIG FAELLE | CRWV | AVGO | 7 | +0.03 | 43% | +0 |
| NEIN | AMD | NVDA | 10 | +0.60 | 10% | +3 |

## Wie schnell muss der Bot sein? (Gewinn gesamt, 5 beste Paare)
| Paar | haltezeit | 0.25s | 0.5s | 1.0s | 2.0s |
|---|---|---|---|---|---|
| NVDA -> SNDK | 2s | -14 | -8 | +44 | +91 |
| NVDA -> SNDK | 5s | +66 | +78 | +86 | +87 |
| NVDA -> SNDK | 15s | +55 | +74 | +89 | +86 |
| AVGO -> NBIS | 2s | +13 | +11 | +0 | -8 |
| AVGO -> NBIS | 5s | +15 | +22 | -3 | -9 |
| AVGO -> NBIS | 15s | +33 | +24 | -2 | -7 |
| MU -> CRWV | 2s | +33 | +32 | +2 | -5 |
| MU -> CRWV | 5s | +52 | +11 | +3 | -0 |
| MU -> CRWV | 15s | +51 | +42 | +1 | -8 |
| NBIS -> AVGO | 2s | -1 | -3 | -2 | +0 |
| NBIS -> AVGO | 5s | +2 | +1 | +0 | +2 |
| NBIS -> AVGO | 15s | +10 | +9 | +6 | +3 |
| CRWV -> AVGO | 2s | -6 | -3 | -3 | -3 |
| CRWV -> AVGO | 5s | -1 | +0 | +0 | -4 |
| CRWV -> AVGO | 15s | +4 | +5 | +5 | +5 |

KANDIDAT = in beiden Haelften Gewinn und deutlich. Trotzdem auf neuen Daten pruefen:
bei 56 Paaren sieht eines leicht durch Zufall gut aus. Die Sprunggrenze nutzt alle
Daten (im Live-Bot erst aus der Vergangenheit berechnen).
