# Folge-Aktien (19 Minuten, Verzoegerung 0.5s, halten 5s, max 10 Stueck)

Spruenge je Aktie: AMD 6, AVGO 2, CRWV 2, MU 2, NBIS 2, NVDA 5, SNDK 2, VRT 2

| Urteil | Wenn | dann | Faelle | Gewinn je Fall | Treffer | Gewinn je 30 min |
|---|---|---|---|---|---|---|
| ZU WENIG FAELLE | SNDK | AVGO | 2 | +16.50 | 50% | +53 |
| ZU WENIG FAELLE | NVDA | AVGO | 5 | +5.60 | 20% | +45 |
| ZU WENIG FAELLE | AVGO | NBIS | 2 | +7.00 | 100% | +22 |
| ZU WENIG FAELLE | SNDK | NBIS | 2 | +5.00 | 50% | +16 |
| ZU WENIG FAELLE | NVDA | SNDK | 5 | +1.50 | 60% | +12 |
| ZU WENIG FAELLE | MU | CRWV | 2 | +2.85 | 50% | +9 |
| ZU WENIG FAELLE | MU | AVGO | 2 | +1.50 | 100% | +5 |
| ZU WENIG FAELLE | VRT | AMD | 2 | +1.00 | 100% | +3 |
| ZU WENIG FAELLE | CRWV | AVGO | 2 | +0.80 | 100% | +3 |
| ZU WENIG FAELLE | SNDK | NVDA | 2 | +0.00 | 50% | +0 |

## Wie schnell muss der Bot sein? (Gewinn gesamt, 5 beste Paare)
| Paar | haltezeit | 0.25s | 0.5s | 1.0s | 2.0s |
|---|---|---|---|---|---|
| SNDK -> AVGO | 2s | +34 | +33 | -3 | -3 |
| SNDK -> AVGO | 5s | +33 | +33 | -1 | +0 |
| SNDK -> AVGO | 15s | +30 | +30 | -4 | -5 |
| NVDA -> AVGO | 2s | -1 | -1 | -0 | -7 |
| NVDA -> AVGO | 5s | +29 | +28 | +28 | +24 |
| NVDA -> AVGO | 15s | +26 | +25 | +28 | +22 |
| AVGO -> NBIS | 2s | +6 | +6 | +3 | -1 |
| AVGO -> NBIS | 5s | +14 | +14 | +5 | -2 |
| AVGO -> NBIS | 15s | +15 | +15 | +5 | -3 |
| SNDK -> NBIS | 2s | +1 | +1 | +0 | -2 |
| SNDK -> NBIS | 5s | +10 | +10 | +4 | -1 |
| SNDK -> NBIS | 15s | +10 | +10 | +4 | -1 |
| NVDA -> SNDK | 2s | -5 | -10 | -5 | +5 |
| NVDA -> SNDK | 5s | +9 | +7 | +14 | +14 |
| NVDA -> SNDK | 15s | +13 | +11 | +17 | +17 |

KANDIDAT = in beiden Haelften Gewinn und deutlich. Trotzdem auf neuen Daten pruefen:
bei 56 Paaren sieht eines leicht durch Zufall gut aus. Die Sprunggrenze nutzt alle
Daten (im Live-Bot erst aus der Vergangenheit berechnen).
