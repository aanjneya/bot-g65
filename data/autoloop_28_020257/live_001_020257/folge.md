# Folge-Aktien (20 Minuten, Verzoegerung 0.5s, halten 5s, max 10 Stueck)

Spruenge je Aktie: AMD 4, AVGO 0, CRWV 1, MU 1, NBIS 0, NVDA 3, SNDK 3, VRT 3

| Urteil | Wenn | dann | Faelle | Gewinn je Fall | Treffer | Gewinn je 30 min |
|---|---|---|---|---|---|---|
| ZU WENIG FAELLE | NVDA | SNDK | 3 | +6.00 | 100% | +27 |
| ZU WENIG FAELLE | VRT | AMD | 3 | +0.27 | 33% | +1 |
| ZU WENIG FAELLE | MU | CRWV | 1 | +0.60 | 100% | +1 |

## Wie schnell muss der Bot sein? (Gewinn gesamt, 5 beste Paare)
| Paar | haltezeit | 0.25s | 0.5s | 1.0s | 2.0s |
|---|---|---|---|---|---|
| NVDA -> SNDK | 2s | -3 | +12 | +9 | +16 |
| NVDA -> SNDK | 5s | +17 | +18 | +16 | +26 |
| NVDA -> SNDK | 15s | +14 | +13 | +12 | +11 |
| VRT -> AMD | 2s | -2 | -2 | -2 | -0 |
| VRT -> AMD | 5s | +1 | +1 | +0 | +1 |
| VRT -> AMD | 15s | +0 | +0 | -1 | +1 |
| MU -> CRWV | 2s | +1 | +1 | -1 | -0 |
| MU -> CRWV | 5s | +1 | +1 | -2 | -2 |
| MU -> CRWV | 15s | +1 | +1 | +0 | +0 |

KANDIDAT = in beiden Haelften Gewinn und deutlich. Trotzdem auf neuen Daten pruefen:
bei 56 Paaren sieht eines leicht durch Zufall gut aus. Die Sprunggrenze nutzt alle
Daten (im Live-Bot erst aus der Vergangenheit berechnen).
