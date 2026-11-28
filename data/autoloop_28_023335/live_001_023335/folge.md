# Folge-Aktien (13 Minuten, Verzoegerung 0.5s, halten 5s, max 10 Stueck)

Spruenge je Aktie: AMD 4, AVGO 1, CRWV 0, MU 1, NBIS 1, NVDA 3, SNDK 1, VRT 3

| Urteil | Wenn | dann | Faelle | Gewinn je Fall | Treffer | Gewinn je 30 min |
|---|---|---|---|---|---|---|
| ZU WENIG FAELLE | AVGO | NBIS | 1 | +17.00 | 100% | +38 |
| ZU WENIG FAELLE | NVDA | AMD | 3 | +4.73 | 67% | +32 |
| ZU WENIG FAELLE | NVDA | SNDK | 3 | +1.93 | 100% | +13 |

## Wie schnell muss der Bot sein? (Gewinn gesamt, 5 beste Paare)
| Paar | haltezeit | 0.25s | 0.5s | 1.0s | 2.0s |
|---|---|---|---|---|---|
| AVGO -> NBIS | 2s | +30 | +13 | +14 | +1 |
| AVGO -> NBIS | 5s | +20 | +17 | +6 | +3 |
| AVGO -> NBIS | 15s | +34 | +19 | +13 | +3 |
| NVDA -> AMD | 2s | -3 | -3 | -3 | -2 |
| NVDA -> AMD | 5s | +14 | +14 | +15 | +9 |
| NVDA -> AMD | 15s | +11 | +12 | +13 | +5 |
| NVDA -> SNDK | 2s | -4 | -4 | +1 | +6 |
| NVDA -> SNDK | 5s | +3 | +6 | +6 | +6 |
| NVDA -> SNDK | 15s | +6 | +7 | +8 | +6 |

KANDIDAT = in beiden Haelften Gewinn und deutlich. Trotzdem auf neuen Daten pruefen:
bei 56 Paaren sieht eines leicht durch Zufall gut aus. Die Sprunggrenze nutzt alle
Daten (im Live-Bot erst aus der Vergangenheit berechnen).
