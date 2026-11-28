# Folge-Aktien (42 Minuten, Verzoegerung 0.5s, halten 5s, max 10 Stueck)

Spruenge je Aktie: AMD 4, AVGO 9, CRWV 5, MU 5, NBIS 5, NVDA 7, SNDK 6, VRT 5

| Urteil | Wenn | dann | Faelle | Gewinn je Fall | Treffer | Gewinn je 30 min |
|---|---|---|---|---|---|---|
| ZU WENIG FAELLE | MU | CRWV | 5 | +10.54 | 100% | +38 |
| ZU WENIG FAELLE | NVDA | SNDK | 7 | +4.36 | 71% | +22 |
| ZU WENIG FAELLE | VRT | MU | 5 | +1.00 | 20% | +4 |

## Wie schnell muss der Bot sein? (Gewinn gesamt, 5 beste Paare)
| Paar | haltezeit | 0.25s | 0.5s | 1.0s | 2.0s |
|---|---|---|---|---|---|
| MU -> CRWV | 2s | +19 | +9 | -0 | -4 |
| MU -> CRWV | 5s | +33 | +53 | +1 | -6 |
| MU -> CRWV | 15s | +43 | +50 | -0 | -6 |
| NVDA -> SNDK | 2s | -11 | -7 | +31 | +41 |
| NVDA -> SNDK | 5s | +55 | +31 | +62 | +66 |
| NVDA -> SNDK | 15s | +57 | +35 | +68 | +71 |
| VRT -> MU | 2s | -5 | -5 | -4 | +1 |
| VRT -> MU | 5s | +5 | +5 | +5 | +2 |
| VRT -> MU | 15s | +6 | +6 | +5 | +1 |

KANDIDAT = in beiden Haelften Gewinn und deutlich. Trotzdem auf neuen Daten pruefen:
bei 56 Paaren sieht eines leicht durch Zufall gut aus. Die Sprunggrenze nutzt alle
Daten (im Live-Bot erst aus der Vergangenheit berechnen).
