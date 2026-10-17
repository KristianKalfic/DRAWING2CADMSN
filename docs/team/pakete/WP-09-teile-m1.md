# WP-09: Teile M1 (Muss-Ziel): Sensoraufnahme, Fuss, Welle

| Profil | Aufwand | Braucht vorher | Gate |
|---|---|---|---|
| B (Läufe starten) + D (Ergebnis prüfen) | 2 h | WP-05 (Auswertung in Schritt 3: WP-04) | **G2, Sa 15:00** |

## Ziel

Muss-Ziel der Challenge: mindestens 3 BCT-Teile als STEP (M1), 100 % gültig (M2), fehlende Maße angenommen und mit Begründung in der Review-Liste (M3). Die Sensoraufnahme ist zugleich unsere Pitch-Demo.

## Regeln für alle Teile-Pakete (WP-09 bis WP-12)

- Das KI-Modell baut jedes Teil **ausschließlich** über die MCP-Tools in der isolierten Sitzung aus WP-05. Niemand schreibt Python für ein einzelnes Teil von Hand.
- Referenz-STEPs und die Hinweise in der Spalte „QA-Hinweise“ unten gehen **nie** in eine Modell-Sitzung. Sie sind nur für die Prüfung danach.
- Ablauf pro Teil:
  1. Teil im Team-Chat eintragen, dann einen Lauf auf einem der drei Wege aus WP-05 starten (Teilname und PDF-Pfad aus der Tabelle in WP-05).
  2. `export_step` liefert `ok: true`. Sonst Protokoll lesen, Ursache notieren.
  3. `results/<teil>/` an die Sammelstelle (WP-05) geben. Dort: `docker run --rm --network none -v "$PWD":/app d2c python scripts/eval_refs.py`.
  4. QA (Profil D): `review.md` gegen die Zeichnung lesen, STEP in einem CAD-Viewer öffnen, orange Flächen ansehen.
  5. MATCH → fertig. Abweichung → Ursache bestimmen:
     - **Lesefehler, den D in der Zeichnung belegt:** neue Sitzung mit dem Hinweis „Maß X in Ansicht Y prüfen“. Nie ein Hinweis, der nur aus dem Referenzvergleich stammt, nie ein Referenzwert (spec.md Abschnitt 3). Im Benchmark zählt der Erstlauf, der Lauf nach Hinweis steht getrennt (Spalte `Attempts` in WP-13).
     - **Echte Unklarheit der Zeichnung:** Es muss einen Review-Eintrag geben, der sie erklärt. Fehlt er, ist das ein Lesefehler wie oben.
  6. Zeile in `docs/results.md` (WP-13), Spalten wie dort beschrieben.
- Teilnamen und PDF-Pfade: Tabelle in WP-05. Wer ein Teil startet, trägt es vorher im Team-Chat ein, damit niemand dasselbe Teil doppelt rechnet.
- Modell und Weg (Desktop-Client, Headless, Agent) je Teil notieren. Für den Pitch zählt: Jedes Teil entstand nur über die MCP-Tools.

## Teile

| Teil | PDF | QA-Hinweise (nur für die Prüfung) |
|---|---|---|
| Sensoraufnahme | `Sensoraufnahme (optional PMI)/Sensoraufnahme.pdf` | Maßstab 2:1. Breite **absichtlich** nicht bemaßt (BCT-Readme), richtig sind 20 mm. Erwartung: Maß als `assumed` mit Messung aus den Vektoren, Review `missing`, beide Seitenflächen orange, PMI-Abstandsmaß „ASSUMED …“. Langloch 5/15 mit Breite 5,5; M8 als Gewindebohrung (Kernloch Ø6,8). |
| Fuss | `Fuss/Fuss_dwg.pdf` | Verdeckte Kanten der Seitenansicht: Ø33 von oben bis zur Plattenoberkante (z 19), darunter Ø19 durch. Hohlkehle R9,5 als Kantenrundung. |
| Welle | `Welle/Welle_dwg.pdf` | Nut 12 breit, Mittenabstand 80, auf `YZ` mit Offset 14. Einstich Ø28 × 2,5 und Fasen als Punkte im Drehprofil. R2 und 1×45° sind nur einmal bemaßt, gelten aber an weiteren Absätzen → `assumed` plus Review. |

## Reihenfolge und BCT

Zuerst die Sensoraufnahme, dann Fuss, dann Welle. BCT ist nur bis Sa 15:15 vor Ort. Jedes M1-Teil, das bis 14:30 fertig ist, geht sofort an WP-16 für den NX-Check 2. Die Teile entstehen zunächst als STEP AP214 mit Farben. AP242 mit PMI kommt mit WP-02 per `replay.py --export` dazu.

## Abnahme (Gate G2)

- Drei Ordner unter `results/` mit `.step`, `review.md`, `report.json`, alle Prüfungen grün.
- `review.md` der Sensoraufnahme nennt die Breite als Annahme mit Begründung. Im STEP sind die Seitenflächen orange markiert (in der Web-Oberfläche oder einem CAD-Viewer geprüft, Screenshot für den Pitch).
- Drei Zeilen in `results/EVAL.md`, jede mit MATCH oder einer Abweichung, die ein Review-Eintrag erklärt.
- `python scripts/replay.py` liefert Exit-Code 0.

## Fallen

- Kantenrundungen erzeugen Zylinderflächen. Deren Ø zählen im MATCH mit, jede Rundung muss also stimmen.
- Nach WP-02 (AP242, Toleranzmitte) alle drei mit `replay.py --export` neu exportieren, ohne neue Modell-Sitzung.
