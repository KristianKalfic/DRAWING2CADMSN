# WP-11: Teile-Bündel B (6 mittlere Teile)

| Profil | Aufwand | Braucht vorher | Gate |
|---|---|---|---|
| B + D | 4 h | WP-09; Getriebedeckel auf Toleranzmitte erst nach WP-02 | G4, Sa 24:00 |

## Ziel

Sechs weitere BCT-Teile, darunter der Getriebedeckel als Vorzeigeteil für Toleranzmitte (X1) und PMI (C1).

## Regeln

Wie in WP-09 („Regeln für alle Teile-Pakete“).

## Teile

| # | Teil | PDF | QA-Hinweise (nur für die Prüfung) |
|---|---|---|---|
| 1 | Flansch | `Flansch/Flansch_dwg.pdf` | Maßstab 2:1. Wände (5) tangential zwischen den Naben, Steg 6: Tasche schneiden, dann die Naben wieder ansetzen. Ø2,5 und Ø3,5 mit Tiefe 10; ob Gewinde → Review. Bohrspitzen werden nicht modelliert. |
| 2 | Trägerplatte | `Trägerplatte/Trägerplatte_dwg.pdf` | Sammelangabe „nicht bemaßte Radien R = 20 mm“ → für jede betroffene Ecke `assumed` plus Review. M5 als Gewindebohrung, Senkungen Ø11/Ø6,6 und Ø10,5/Ø5,5. Wanddicke 6 gegenüber 6,5 in den Schnitten → Review `conflict`. |
| 3 | Dreistern | `Dreistern/Dreistern_dwg.pdf` | Arme unter 120°, Anzugswinkel 102°, R20 und R1. Gewölbte Oberseite eventuell nur mit `run_script` möglich, dann Review. |
| 4 | Befestigung | `Befestigung/Befestigung_dwg.pdf` | Gabel R30/R40, 3× Ø12 unter 45°, schräge Rippe. Ebenen aus der Seitenansicht. |
| 5 | Maschinenteil | `Maschinenteil/Maschinenteil_dwg.pdf` | 12× Ø10 auf Ø76 mit 25,5° Teilung. Detail A (2:1). M10 außen als Ø10 plus Review `convention`. Sammelangabe „nicht bemaßte Radien R=1“. |
| 6 | Getriebedeckel | `Getriebedeckel (optional PMI)/Getriebedeckel.pdf` | Maßstab 2:1. Drehprofil mit R6, R1, Ringnut auf der Flanschunterseite, 4× Ø6,6 H7 auf Ø55 (`polar`). Abmaße Ø68 +0,2/0, Ø28 0/−0,05, 16 0/−0,1. **Vorzeigeteil:** zusätzlich `export_step(variant="mid")`; `review.md` muss im Abschnitt Tolerances zeigen, welche Maße auf Mitte gelegt wurden und ob alle eingehalten sind. PMI mit BCTs `Getriebedeckel_PMI.stp` vergleichen (dort 16 semantische Maße). |

## Abnahme

- Jedes Teil mit Zeile in `docs/results.md`.
- Getriebedeckel: `Getriebedeckel_mid.step` und Toleranztabelle in `review.md`, Screenshot oder Tabellenausschnitt an WP-17.
