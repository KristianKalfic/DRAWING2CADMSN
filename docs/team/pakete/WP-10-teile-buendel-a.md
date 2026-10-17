# WP-10: Teile-Bündel A (5 einfache Teile)

| Profil | Aufwand | Braucht vorher | Gate |
|---|---|---|---|
| B + D | 3 h | WP-09 | G3, Sa 20:00 |

## Ziel

Fünf weitere BCT-Teile für den Benchmark. Mit M1 sind es dann 8 von 21. Darunter zwei Zeichnungen mit falschem Schriftfeld-Maßstab: Die Erkennung als `conflict` ist ein starkes Pitch-Beispiel.

## Regeln

Genau wie in WP-09 („Regeln für alle Teile-Pakete“): nur über MCP, Referenzen und QA-Hinweise nie in die Sitzung, Ablauf in sechs Schritten, Zeile in `docs/results.md`.

## Teile

| # | Teil | PDF | QA-Hinweise (nur für die Prüfung) |
|---|---|---|---|
| 1 | Achse | `Achse/Achse.pdf` | Maßstab 2,5:1. Drehprofil: Flansch Ø13 × 1,1, Schaft Ø10 h6, Nut Ø9,6 h10, Fase 0,3×45°. M6 als Gewindebohrung, Bohrtiefe 14, Gewindetiefe 9. Bohrspitze 118° → `assumed` plus Review. |
| 2 | Schnittmenge | `Schnittmenge/Schnittmenge.pdf` | **Schriftfeld 1:1, gezeichnet etwa 0,75** → Review `conflict`. Schnitt zweier Extrusionen (`mode="intersect"`). Langlochlänge 36 = 40 − 2·2 nur abgeleitet → `assumed`. |
| 3 | 3-Loch-Hebel | `3-Loch-Hebel/3-Loch-Hebel.pdf` | **Schriftfeld 1:1, gezeichnet 2:1** (alle Kreise doppelt so groß wie bemaßt) → Review `conflict`. Kontur aus drei Augen R9/R14,5 mit Stegen 18 breit, Winkel 50°, R10-Übergänge als `fillets` im Pfadprofil. Fasen 1×45° an allen Bohrungen beidseitig. |
| 4 | Haken | `Haken/Haken_dwg.pdf` | Platte 20 dick (z ±10), Ring Ø152 30 dick (z ±15) laut Schnitt A-A. Kontur als Pfad mit scharfen Ecken plus `fillets` für R35, R35, R16. |
| 5 | Rotation | `Rotation/Rotation_dwg.pdf` | Zeichnungstitel „Drehteil“ ≠ Ordnername → Review. Maßstab 1:2. Fase 5×45°, Detail B (2:1) mit „1“. 6× Ø12 auf Ø50 als Kreismuster (`polar`). |

## Abnahme

- Jedes Teil hat eine Zeile in `docs/results.md`: MATCH oder Abweichung mit Grund.
- 3-Loch-Hebel und Schnittmenge: Der Maßstabskonflikt steht als `conflict` in `review.md`. Screenshot für den Pitch an WP-17.
