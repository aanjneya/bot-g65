# Folge-Aktien (31 Minuten, Verzoegerung 0.5s, halten 5s, max 10 Stueck)

Spruenge je Aktie: AMD 5, AVGO 5, CRWV 4, MU 4, NBIS 5, NVDA 5, SNDK 4, VRT 5

| Urteil | Wenn | dann | Faelle | Gewinn je Fall | Treffer | Gewinn je 30 min |
|---|---|---|---|---|---|---|
| ZU WENIG FAELLE | AMD | NVDA | 5 | +16.00 | 20% | +78 |
| ZU WENIG FAELLE | MU | CRWV | 4 | +13.47 | 50% | +53 |
| ZU WENIG FAELLE | NVDA | SNDK | 5 | +5.00 | 100% | +25 |
| ZU WENIG FAELLE | MU | VRT | 4 | +4.10 | 25% | +16 |
| ZU WENIG FAELLE | AVGO | NBIS | 5 | +2.64 | 100% | +13 |
| ZU WENIG FAELLE | AMD | SNDK | 5 | +2.22 | 20% | +11 |
| ZU WENIG FAELLE | VRT | MU | 5 | +1.00 | 20% | +5 |
| ZU WENIG FAELLE | SNDK | AMD | 4 | +0.07 | 25% | +0 |

## Wie schnell muss der Bot sein? (Gewinn gesamt, 5 beste Paare)
| Paar | haltezeit | 0.25s | 0.5s | 1.0s | 2.0s |
|---|---|---|---|---|---|
| AMD -> NVDA | 2s | +77 | +77 | +79 | +80 |
| AMD -> NVDA | 5s | +80 | +80 | +80 | +63 |
| AMD -> NVDA | 15s | +79 | +79 | +79 | +80 |
| MU -> CRWV | 2s | +17 | +14 | +14 | -3 |
| MU -> CRWV | 5s | +55 | +54 | +1 | -0 |
| MU -> CRWV | 15s | +59 | +58 | +18 | -6 |
| NVDA -> SNDK | 2s | -4 | +23 | +34 | +47 |
| NVDA -> SNDK | 5s | +22 | +25 | +57 | +55 |
| NVDA -> SNDK | 15s | +48 | +56 | +57 | +58 |
| MU -> VRT | 2s | +16 | +17 | +15 | -5 |
| MU -> VRT | 5s | +15 | +16 | +17 | -7 |
| MU -> VRT | 15s | +15 | +16 | +16 | -6 |
| AVGO -> NBIS | 2s | +24 | +22 | -3 | -3 |
| AVGO -> NBIS | 5s | +25 | +13 | -1 | -3 |
| AVGO -> NBIS | 15s | +25 | +25 | +1 | -4 |

KANDIDAT = in beiden Haelften Gewinn und deutlich. Trotzdem auf neuen Daten pruefen:
bei 56 Paaren sieht eines leicht durch Zufall gut aus. Die Sprunggrenze nutzt alle
Daten (im Live-Bot erst aus der Vergangenheit berechnen).
