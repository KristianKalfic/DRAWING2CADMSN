# WP-12: Teile-Bündel C (7 schwere Teile)

| Profil | Aufwand | Braucht vorher | Gate |
|---|---|---|---|
| B + D | 5 h, über Nacht verteilt | WP-09 | G5, So 09:00 |

## Ziel

So viele der schweren Teile wie möglich. Was nicht gelingt, steht mit ehrlicher Begründung unter „Limitations“ im README. Auch ein Teilergebnis mit vollständiger Review-Liste zählt: Es zeigt, dass das System seine Grenzen kennt, statt zu raten.

## Regeln

Wie in WP-09. Reihenfolge wie in der Tabelle, schwerste zuletzt. Über Nacht im Schichtbetrieb, damit immer jemand die Läufe betreut.

## Teile

| # | Teil | PDF | QA-Hinweise (nur für die Prüfung) |
|---|---|---|---|
| 1 | Lagerschaft | `Lagerschaft/Lagerschaft.pdf` | Entformungsschräge 2° (`taper`), neutrale Ebene am Fuß → `assumed` plus Review. Hülle um Nabe und Augen als `hull`-Profil. R2 als Kantenrundung. |
| 2 | Fixierung | `Fixierung/Fixierung.pdf` | Rohr Ø30/Ø24 unter 45° auf `custom`-Ebene, Querbohrung Ø5 entlang freier Achse. „3x Senkbohrung für M4“ ohne Maße → Normwerte annehmen plus Review. Sammelnotiz Fasen 0,5×45° → je Kante `assumed`. |
| 3 | Splint | `Splint/Splint.pdf` | **Schriftfeld 1:1, gezeichnet 2:1** → Review `conflict`. Halbrunddraht: Schenkel per Extrusion mit Pfad, Auge per Rotation mit Profil, Hals notfalls `run_script`. Augen-Innen-Ø nicht gedruckt → `assumed`. |
| 4 | Grundplatte | `Grundplatte/Grundplatte.pdf` | **Schriftfeld 1:1, gezeichnet 1:2** → Review `conflict`. Formwanne mit 3°-Schrägen, Wand 3, rund 100 Rundungen aus Sammelnotizen. Teilergebnis mit vollständiger Review-Liste ist akzeptabel. |
| 5 | Komplexe Zeichnung 3 | `Komplexe Zeichnung 3/1 763 1487 0.pdf` | Echte Textebene: das Modell soll zuerst `read_drawing_text` nutzen. Hauptansichten 2:1, Einzelheit W 5:1. 12 Abmaße, 6 Passungen mit Ⓔ, 10 Form- und Lagetoleranzen → alle in die Review-Liste. Innengewinde mit `thread_core="minor"` (Konvention dieser Referenzen). |
| 6 | Komplexe Zeichnung 1 | `Komplexe Zeichnung 1/1 106 0168 3.pdf` | 4 Seiten, 39 verschiedene Durchmesser. Nur wenn Zeit bleibt. |
| 7 | Komplexe Zeichnung 2 | `Komplexe Zeichnung 2/1 124 0146 0.pdf` | 3 Seiten, 15 Durchmesser. **Referenz ungültig** → MATCH-Spalte zeigt „reference invalid“. Nur wenn Zeit bleibt. |

## Abnahme

- Jedes versuchte Teil hat eine Zeile in `docs/results.md`, auch die gescheiterten (mit Grund).
- Die Grenzen (welche Teile, warum) gehen an WP-14 (README Limitations) und WP-17 (Pitch, Folie Ergebnisse).

## Schnittlinie

Reißt G4 nach 02:00, entfallen Komplexe Zeichnung 1 und 2 ganz. Das README nennt sie dann als ehrliche Grenze.
