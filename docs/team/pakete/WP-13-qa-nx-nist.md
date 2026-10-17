# WP-13: QA: Review-Listen, NX-Check, NIST-Prüfung, Ergebnistabelle

| Profil | Aufwand | Braucht vorher | Gate |
|---|---|---|---|
| D (Konstruktion und QA) | laufend ab G1 | WP-09 | NX bis Sa 15:15, Tabelle G3 bis G5 |

## Ziel

Jede Zahl, die wir zeigen, ist geprüft. Die Jury von BCT sind CAD-Profis: Ein falsch gelesenes Maß im Pitch kostet mehr als ein fehlendes Teil. Außerdem liefert dieses Paket die Belege für M2 („öffnet fehlerfrei in einem CAD-System wie NX“) und C2 („PMI-Validierung“).

## Dateien

`docs/results.md`, Screenshots unter `docs/img/` (nur Demo-Teil oder freigegebene BCT-Teile, WP-16). Auf diesem Rechner liegt die Sammelstelle für `results/` (WP-05). Ergebnisse kommen nur lokal an (AirDrop, USB-Stick oder Kabel).

## Aufgaben

1. **Review-Listen prüfen.** Für jedes fertige Teil `results/<teil>/review.md` neben die Zeichnung legen:
   - Stimmt jede Annahme fachlich (z. B. Breite der Sensoraufnahme = 20, weil Bogenradius = halbe Breite)?
   - Fehlt eine Unklarheit, die ein Konstrukteur sofort sehen würde?
   - Nur Lesefehler, die du in der Zeichnung belegen kannst, gehen als „Maß X in Ansicht Y prüfen“ an die Person, die das Teil rechnet. Was nur der Referenzvergleich zeigt, bleibt draußen (spec.md Abschnitt 3).
2. **Im CAD-Viewer öffnen.** Jedes STEP in einem freien Viewer (FreeCAD, CAD Assistant oder ein Online-Viewer **nur für unsere Demo-Teile**, nie für BCT-Daten) öffnen: Körper vollständig? Orange Flächen dort, wo die Annahme ist? Grüne Gewindebohrungen?
3. **NX-Check mit BCT** (zusammen mit WP-16, BCT nur bis Sa 15:15 vor Ort): NX-Check 1 gegen 12:30 mit dem Demo-STEP (AP242, PMI, Farben), NX-Check 2 ab 14:30 mit den fertigen M1-Teilen, zuerst Sensoraufnahme. Ergebnis (öffnet fehlerfrei, Farben sichtbar, PMI sichtbar) mit Foto oder Screenshot festhalten. Das ist der stärkste Beleg für M2. Spätere Teile (Getriebedeckel, 3-Loch-Hebel) nur, wenn BCT einen Weg nach 15:15 anbietet (WP-16, Frage 4).
4. **NIST STEP File Analyzer** (C2, nur Windows mit Excel): SFA von `github.com/usnistgov/SFA` (Ordner `Release`), die finalen AP242-Dateien prüfen. Der erste Start installiert IFCsvr. Ziel: keine roten Meldungen im Status-Tab, Screenshot für Deck und README. Auf dem Mac prüft `read_pmi` automatisch (läuft bei jedem Export). Ohne Windows-Rechner gilt C2 als teilweise belegt: PMI geschrieben und automatisch zurückgelesen, aber ohne NIST-Bericht.
5. **Ergebnistabelle** `docs/results.md` (Englisch), Spalten:
   - `Part`, `Scale (title block / drawn)`, `Scale conflict` (hat der Lauf den falschen Maßstab als `conflict` gemeldet? Nur bei den vier betroffenen Teilen),
   - `MATCH`, `Attempts` (1 = Erstlauf, 2 = nach einem Hinweis aus der Zeichnung), `ΔV %`, `ΔBBox mm`, `Review items`,
   - `run_script ops` (Anzahl Ops mit `"op": "script"` in `ops.json`, belegt R7), `Model`, `Client` (Desktop, Headless, Agent), `Runtime`, `Cost` (aus `usage` des Agenten, sonst „–“), `FreeCAD` (aus WP-19), `Notes`.
   Keine Maße der BCT-Teile, solange BCT die Veröffentlichung nicht freigegeben hat.
   Darunter Summen: x von 21 MATCH im Erstlauf, y nach Hinweis, Anteil der exportierten STEPs mit grünen Server-Prüfungen, z Review-Einträge, Maßstabskonflikt in a von 4 Fällen erkannt, b Teile ohne `run_script`. Dazu eine ehrliche Liste der Abweichungen mit Grund.
6. Zahlen zu jedem Gate an WP-17 (Pitch) melden.

## Abnahme

- `docs/results.md` enthält jedes versuchte Teil, Summen stimmen mit `results/EVAL.json` überein.
- Mindestens ein NX-Beleg oder die dokumentierte Antwort von BCT, falls kein NX-Termin möglich war.
- NIST-Screenshot, falls ein Windows-Rechner mit Excel im Team ist. Sonst steht im README, dass `read_pmi` die automatische Prüfung übernimmt.

## Fallen

- BCT-Dateien und daraus erzeugte Modelle nie auf fremde Online-Dienste hochladen und nie ins Repo committen, bis BCT es freigibt (WP-16). Erlaubt ist nur die Modell-Sitzung des Laufs selbst (Challenge-Regel).
- Ergebnisse nie schönen. „7 von 21 MATCH, 14 mit erklärter Abweichung“ ist stärker als eine Zahl, die bei Nachfrage nicht hält.
