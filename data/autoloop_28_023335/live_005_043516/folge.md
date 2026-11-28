# Folge-Aktien (60 Minuten, Verzoegerung 0.5s, halten 5s, max 10 Stueck)

Spruenge je Aktie: AMD 7, AVGO 8, CRWV 9, MU 10, NBIS 7, NVDA 16, SNDK 8, VRT 7

| Urteil | Wenn | dann | Faelle | Gewinn je Fall | Treffer | Gewinn je 30 min |
|---|---|---|---|---|---|---|
| KANDIDAT | NVDA | SNDK | 16 | +5.90 | 69% | +47 |
| KANDIDAT | MU | CRWV | 10 | +2.83 | 70% | +14 |
| ZU WENIG FAELLE | AVGO | NBIS | 8 | +5.41 | 88% | +22 |
| NEIN | NVDA | AVGO | 16 | +3.18 | 25% | +25 |

## Wie schnell muss der Bot sein? (Gewinn gesamt, 5 beste Paare)
| Paar | haltezeit | 0.25s | 0.5s | 1.0s | 2.0s |
|---|---|---|---|---|---|
| NVDA -> SNDK | 2s | -19 | -29 | +20 | +49 |
| NVDA -> SNDK | 5s | +95 | +94 | +83 | +55 |
| NVDA -> SNDK | 15s | +100 | +97 | +88 | +60 |
| MU -> CRWV | 2s | +17 | +28 | -15 | -24 |
| MU -> CRWV | 5s | +32 | +28 | +2 | -23 |
| MU -> CRWV | 15s | +69 | +74 | -11 | -24 |
| AVGO -> NBIS | 2s | +28 | +25 | +1 | -3 |
| AVGO -> NBIS | 5s | +51 | +43 | +6 | -5 |
| AVGO -> NBIS | 15s | +85 | +83 | +5 | +8 |
| NVDA -> AVGO | 2s | -18 | -17 | -15 | +105 |
| NVDA -> AVGO | 5s | +47 | +51 | +49 | +106 |
| NVDA -> AVGO | 15s | +87 | +119 | +123 | +45 |

KANDIDAT = in beiden Haelften Gewinn und deutlich. Trotzdem auf neuen Daten pruefen:
bei 56 Paaren sieht eines leicht durch Zufall gut aus. Die Sprunggrenze nutzt alle
Daten (im Live-Bot erst aus der Vergangenheit berechnen).
