# Folge-Aktien (46 Minuten, Verzoegerung 0.5s, halten 5s, max 10 Stueck)

Spruenge je Aktie: AMD 2, AVGO 2, CRWV 4, MU 4, NBIS 1, NVDA 8, SNDK 4, VRT 5

| Urteil | Wenn | dann | Faelle | Gewinn je Fall | Treffer | Gewinn je 30 min |
|---|---|---|---|---|---|---|
| KANDIDAT | NVDA | SNDK | 8 | +9.28 | 62% | +48 |
| ZU WENIG FAELLE | AVGO | NVDA | 2 | +19.35 | 100% | +25 |
| ZU WENIG FAELLE | MU | CRWV | 4 | +5.20 | 75% | +13 |
| ZU WENIG FAELLE | AVGO | NBIS | 2 | +1.00 | 50% | +1 |
| ZU WENIG FAELLE | NBIS | AVGO | 1 | +1.00 | 100% | +1 |
| ZU WENIG FAELLE | NBIS | NVDA | 1 | +0.20 | 100% | +0 |

## Wie schnell muss der Bot sein? (Gewinn gesamt, 5 beste Paare)
| Paar | haltezeit | 0.25s | 0.5s | 1.0s | 2.0s |
|---|---|---|---|---|---|
| NVDA -> SNDK | 2s | -12 | +15 | +22 | +61 |
| NVDA -> SNDK | 5s | +64 | +74 | +76 | +55 |
| NVDA -> SNDK | 15s | +50 | +51 | +51 | +34 |
| AVGO -> NVDA | 2s | -2 | -1 | -0 | +38 |
| AVGO -> NVDA | 5s | +38 | +39 | +39 | +38 |
| AVGO -> NVDA | 15s | +22 | +23 | +17 | +38 |
| MU -> CRWV | 2s | +19 | +14 | +1 | -1 |
| MU -> CRWV | 5s | +35 | +21 | +2 | +1 |
| MU -> CRWV | 15s | +45 | +22 | +1 | -1 |
| AVGO -> NBIS | 2s | +8 | +2 | -0 | -2 |
| AVGO -> NBIS | 5s | +8 | +2 | -0 | -1 |
| AVGO -> NBIS | 15s | +4 | -0 | -1 | -1 |
| NBIS -> AVGO | 2s | -1 | +0 | -1 | -1 |
| NBIS -> AVGO | 5s | +0 | +1 | +0 | +0 |
| NBIS -> AVGO | 15s | -3 | -2 | -4 | -4 |

KANDIDAT = in beiden Haelften Gewinn und deutlich. Trotzdem auf neuen Daten pruefen:
bei 56 Paaren sieht eines leicht durch Zufall gut aus. Die Sprunggrenze nutzt alle
Daten (im Live-Bot erst aus der Vergangenheit berechnen).
