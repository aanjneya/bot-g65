# Folge-Aktien (14 Minuten, Verzoegerung 0.5s, halten 5s, max 10 Stueck)

Spruenge je Aktie: AMD 0, AVGO 7, CRWV 4, MU 4, NBIS 6, NVDA 2, SNDK 2, VRT 4

| Urteil | Wenn | dann | Faelle | Gewinn je Fall | Treffer | Gewinn je 30 min |
|---|---|---|---|---|---|---|
| ZU WENIG FAELLE | AVGO | NBIS | 7 | +6.71 | 71% | +98 |
| ZU WENIG FAELLE | MU | CRWV | 4 | +10.70 | 100% | +89 |
| ZU WENIG FAELLE | NVDA | SNDK | 2 | +6.75 | 100% | +28 |

## Wie schnell muss der Bot sein? (Gewinn gesamt, 5 beste Paare)
| Paar | haltezeit | 0.25s | 0.5s | 1.0s | 2.0s |
|---|---|---|---|---|---|
| AVGO -> NBIS | 2s | +53 | +34 | +4 | -9 |
| AVGO -> NBIS | 5s | +47 | +47 | +7 | -5 |
| AVGO -> NBIS | 15s | +57 | +49 | +13 | -3 |
| MU -> CRWV | 2s | +38 | +27 | +3 | -4 |
| MU -> CRWV | 5s | +33 | +43 | +8 | -0 |
| MU -> CRWV | 15s | +61 | +45 | +10 | -1 |
| NVDA -> SNDK | 2s | -2 | -1 | +6 | +22 |
| NVDA -> SNDK | 5s | +19 | +13 | +24 | +22 |
| NVDA -> SNDK | 15s | +23 | +14 | +25 | +22 |

KANDIDAT = in beiden Haelften Gewinn und deutlich. Trotzdem auf neuen Daten pruefen:
bei 56 Paaren sieht eines leicht durch Zufall gut aus. Die Sprunggrenze nutzt alle
Daten (im Live-Bot erst aus der Vergangenheit berechnen).
