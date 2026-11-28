# Folge-Aktien (14 Minuten, Verzoegerung 0.5s, halten 5s, max 10 Stueck)

Spruenge je Aktie: AMD 2, AVGO 1, CRWV 1, MU 1, NBIS 1, NVDA 1, SNDK 0, VRT 1

| Urteil | Wenn | dann | Faelle | Gewinn je Fall | Treffer | Gewinn je 30 min |
|---|---|---|---|---|---|---|
| ZU WENIG FAELLE | MU | CRWV | 1 | +7.20 | 100% | +16 |
| ZU WENIG FAELLE | AVGO | NBIS | 1 | +5.00 | 100% | +11 |
| ZU WENIG FAELLE | AVGO | VRT | 1 | +0.90 | 100% | +2 |
| ZU WENIG FAELLE | NBIS | AMD | 1 | +0.80 | 100% | +2 |

## Wie schnell muss der Bot sein? (Gewinn gesamt, 5 beste Paare)
| Paar | haltezeit | 0.25s | 0.5s | 1.0s | 2.0s |
|---|---|---|---|---|---|
| MU -> CRWV | 2s | +16 | +19 | +2 | -1 |
| MU -> CRWV | 5s | +10 | +7 | +2 | -1 |
| MU -> CRWV | 15s | +25 | +24 | +2 | -0 |
| AVGO -> NBIS | 2s | +6 | +5 | +1 | +1 |
| AVGO -> NBIS | 5s | +6 | +5 | +1 | +1 |
| AVGO -> NBIS | 15s | +5 | +4 | +1 | +1 |
| AVGO -> VRT | 2s | +0 | +0 | +0 | +0 |
| AVGO -> VRT | 5s | +1 | +1 | +1 | +0 |
| AVGO -> VRT | 15s | +0 | +0 | +0 | -1 |
| NBIS -> AMD | 2s | +0 | +0 | +1 | -1 |
| NBIS -> AMD | 5s | +0 | +1 | +1 | +0 |
| NBIS -> AMD | 15s | +4 | +4 | +4 | +4 |

KANDIDAT = in beiden Haelften Gewinn und deutlich. Trotzdem auf neuen Daten pruefen:
bei 56 Paaren sieht eines leicht durch Zufall gut aus. Die Sprunggrenze nutzt alle
Daten (im Live-Bot erst aus der Vergangenheit berechnen).
