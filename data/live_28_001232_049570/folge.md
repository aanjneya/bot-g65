# Folge-Aktien (60 Minuten, Verzoegerung 0.5s, halten 5s, max 10 Stueck)

Spruenge je Aktie: AMD 8, AVGO 12, CRWV 4, MU 4, NBIS 9, NVDA 11, SNDK 10, VRT 10

| Urteil | Wenn | dann | Faelle | Gewinn je Fall | Treffer | Gewinn je 30 min |
|---|---|---|---|---|---|---|
| KANDIDAT | AVGO | NBIS | 12 | +13.22 | 83% | +79 |
| ZU WENIG FAELLE | NVDA | SNDK | 11 | +14.33 | 91% | +79 |
| ZU WENIG FAELLE | MU | CRWV | 4 | +13.80 | 75% | +28 |

## Wie schnell muss der Bot sein? (Gewinn gesamt, 5 beste Paare)
| Paar | haltezeit | 0.25s | 0.5s | 1.0s | 2.0s |
|---|---|---|---|---|---|
| AVGO -> NBIS | 2s | +122 | +113 | +20 | -4 |
| AVGO -> NBIS | 5s | +156 | +159 | +34 | +6 |
| AVGO -> NBIS | 15s | +154 | +114 | +32 | -2 |
| NVDA -> SNDK | 2s | -8 | +65 | +38 | +153 |
| NVDA -> SNDK | 5s | +151 | +158 | +129 | +134 |
| NVDA -> SNDK | 15s | +153 | +156 | +159 | +160 |
| MU -> CRWV | 2s | +39 | +33 | +13 | -2 |
| MU -> CRWV | 5s | +58 | +55 | +23 | +3 |
| MU -> CRWV | 15s | +64 | +64 | +28 | +10 |

KANDIDAT = in beiden Haelften Gewinn und deutlich. Trotzdem auf neuen Daten pruefen:
bei 56 Paaren sieht eines leicht durch Zufall gut aus. Die Sprunggrenze nutzt alle
Daten (im Live-Bot erst aus der Vergangenheit berechnen).
