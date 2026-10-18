# Drawing2CAD: Systemspezifikation

## 1. Zweck

Drawing2CAD erzeugt aus einer technischen 2D-Zeichnung (Vektor-PDF) ein geprüftes 3D-Volumenmodell im STEP-Format. Ein Sprachmodell liest die Zeichnung und baut das Teil ausschließlich über die Werkzeuge eines lokal laufenden MCP-Servers. Der Server prüft nach jedem Bauschritt den Volumenkörper, die Maße und den STEP-Export und meldet das Ergebnis maschinenlesbar zurück. Jede Annahme steht mit Begründung in einer Prüfliste und ist im STEP farbig markiert.

## 2. Geltungsbereich

| Enthalten | Nicht enthalten |
| --- | --- |
| Einzelteile aus genau einem Volumenkörper | Baugruppen |
| Prismatische und rotationssymmetrische Geometrie, Bohrungen mit Senkung und Gewinde-Kernloch, Rundungen, Fasen, Entformungsschrägen | Freiformflächen, Gewindegeometrie |
| Zeichnungen als PDF oder als PNG und JPG, die zu einer einseitigen PDF-Datei umgewandelt werden; Messung aus der Vektorgeometrie | Messung in Rasterbildern und Scans |
| STEP mit farbig markierten Annahmen | Native CAD-Formate |

## 3. Anforderungen

| ID | Anforderung | Umsetzung |
| --- | --- | --- |
| A1 | Konturen, Maße, Bohrungen, Grundmerkmale und Hinweise einer Zeichnung erkennen | `render_drawing`, `read_drawing_text`, `measure_drawing`; Ergebnis ist die `PartSpec` (5.1) |
| A2 | Mehrdeutige oder fehlende Information markieren statt ungeprüft interpretieren | Prüfvermerkpflicht der `PartSpec` (5.1), Prüfliste (9) |
| A3 | Das 3D-Modell entsteht ausschließlich über den MCP-Server und wird als STEP exportiert | Bauwerkzeuge und `export_step` (6) |
| A4 | Der MCP-Server läuft vollständig lokal, ohne Cloud-Abhängigkeit | Docker-Image, Transport stdio, Start mit `--network none` (14) |
| A5 | Modellskripte laufen isoliert: ohne Netzwerk, mit Zeitlimit | Unterprozess je Modellaufbau mit Zeitlimit und Ressourcengrenzen, Positivliste für Skripte, Container als Nutzer ohne Rechte (14) |
| A6 | Skriptfähiger quelloffener CAD-Kern auf OpenCascade-Basis | CadQuery 2.8.0 mit OCP 7.9.3 |
| A7 | Wenige Werkzeuge mit strukturierten Parametern, freier Code nur als Rückfall | 15 Werkzeuge, `run_script` als Rückfall (6) |
| A8 | Nach jedem Modellaufbau prüfen und maschinenlesbar zurückmelden: gültiger Volumenkörper, Maße gegen die erfasste Spezifikation (7), fehlerfreier STEP-Rückimport; gedruckte Maße, die im Körper fehlen, als Hinweis (`dims_found`) | `cad/checks.py`, BuildReport (5.3, 7) |
| A9 | Unabhängig vom verwendeten Sprachmodell | MCP-Standard, Arbeitsanleitung `instructions.md`, Eingabeschicht `server/coerce.py`, kompakte Antworten mit `next` (6.3, 6.4), Lite-Modus des Agenten (4.6) |
| A10 | Unklare Information im 3D-Modell markieren | Orange Flächen, benannt nach dem Prüfeintrag (8.1) |
| A11 | Fehlende Maße aus der Zeichnungsgeometrie annehmen und mit Begründung führen | `measure_drawing`, `assumed` mit `assumption_basis` und Prüfeintrag (5.1) |
| A12 | Toleranzen auslesen, auf Toleranzmitte legen, Einhaltung prüfen | Textebene liest Toleranzen und Passungen; `quality/tolerances.py` (10), `export_step` mit `variant="mid"` (8.1) |
| A13 | STEP AP242 mit semantischem PMI und GLB für die 3D-Ansicht | `cad/export.py`, genutzt von `export_step` (8) |
| A14 | Zeichnung hochladen und automatisch analysieren | Web-Oberfläche: Ablegen startet den Lauf mit dem gewählten Modell (12) |
| A15 | PMI der STEP-Datei prüfen | `read_pmi` liest die geschriebenen Maße zurück; Prüfzeile `STEP PMI read back` und Abschnitt „PMI in the STEP file“ der Prüfliste (7, 8.2, 9) |

## 4. Architektur

### 4.1 Überblick

![Architektur](architektur.png)

Quelle der Grafik: `architektur.excalidraw` (Excalidraw).

### 4.2 Ablauf

1. **Zeichnung lesen:** `render_drawing` für jede Seite und für Ausschnitte, `read_drawing_text` für die Textebene, `measure_drawing` für Maße aus der Vektorgeometrie und die Prüfung des Maßstabs.
2. **Beschreibung einreichen:** `check_spec` meldet unmögliche Werte vorab, `submit_spec` prüft die `PartSpec` gegen Schema, Konsistenz- und Prüfvermerkregeln und legt das Teil an.
3. **Bauen:** `extrude`, `revolve`, `add_holes` und `edge_finish`, als Rückfall `run_script`. Jede Änderung baut alle Operationen in einem neuen Unterprozess neu und prüft das Ergebnis. Die Antwort nennt die nicht bestandenen Prüfungen und die nächsten Schritte (6.4).
4. **Korrigieren:** `edit_op`, `delete_op` und `move_op` ändern die Liste der Operationen, `get_state` zeigt den Stand.
5. **Exportieren:** `export_step` schreibt bei bestandenen Prüfungen STEP AP242 mit semantischem PMI, dazu GLB, `marks.json`, `pmi.json`, Prüfbericht und Prüfliste.

### 4.3 Entwurfsentscheidungen

1. **Das Modell ist eine Liste von Operationen.** Nach jeder Änderung führt der Server alle Operationen in einem neuen Prozess erneut aus. Jeder Neuaufbau ist damit isoliert, aus `ops.json` reproduzierbar und per `edit_op` änderbar.
2. **Explizite globale Koordinaten statt Flächen-Selektoren.** Jede Operation nennt ihre Ebene (`XY`, `XZ`, `YZ` oder `custom`, verschoben entlang der Normalen) und ihre Position. Kleinere Sprachmodelle arbeiten damit zuverlässiger.
3. **Kantenauswahl für Rundungen und Fasen anhand naher Punkte** (`near`). Findet die Auswahl keine Kante, ist das ein Fehler; die Operation wird nicht stillschweigend übersprungen.
4. **Prüfvermerkpflicht im Schema.** Jedes Maß mit `assumed = true` oder `confidence < 0.8` muss in einem Prüfeintrag stehen, sonst lehnt `submit_spec` die Beschreibung ab.
5. **Markierung im Modell.** Flächen nahe den Punkten `mark_near` eines Prüfeintrags werden im STEP orange gefärbt und nach dem Eintrag benannt. Hat der Eintrag ein angenommenes Maß mit PMI, sind genau die Flächen dieses PMI die Markierung.
6. **Isolation in Schichten.** Container ohne Netzwerk, als Nutzer ohne Rechte und mit schreibgeschütztem Dateisystem; Modellaufbau in einem Unterprozess mit Zeitlimit und Ressourcengrenzen; für `run_script` ein schmaler Namensraum (Positivliste) mit einer Prüfung auf dem Syntaxbaum. Die Prüfung liefert verständliche Fehlermeldungen; die Grenze bilden Container und Prozess (14).
7. **Der Server kennt keine Referenzdaten.** Er liest nur PDF-Zeichnungen unterhalb von `D2C_DATA`.
8. **Server und Sprachmodell sind getrennt.** Der Server braucht kein Netzwerk. Jeder MCP-Client kann ihn nutzen.
9. **Zwei Phasen, ein Vertrag.** In der Lesephase füllt ein Modell mit Bildverständnis die `PartSpec`. In der Bauphase setzt ein beliebiges Modell sie in Operationen um, auch ohne Bildverständnis.
10. **Rückverfolgbarkeit.** Jeder Wert einer Operation, der einem gedruckten Maß entspricht, ist über `refs` an dessen id gebunden. Daraus entsteht der Abschnitt „Traceability“ der Prüfliste. Ungebundene Größen stehen im Abschnitt „Values without dimension reference“.
11. **Die Web-Oberfläche hat keinen eigenen Teilzustand.** Sie zeigt den Ergebnisordner; Uploads liegen im Unterordner `uploads/` der Zeichnungen, laufende Agentenläufe nur im Speicher des Web-Prozesses.
12. **Umdeuten statt ablehnen, aber nie still.** Die Eingabeschicht `server/coerce.py` korrigiert Formfehler der Werkzeugargumente (Zahl als Text, Alias eines Feldes, Punktliste in anderer Form) und meldet jede Umdeutung unter `coerced`. Die Abnahmeschwellen (7) bleiben unverändert: Die Schicht ändert nur die Form einer Eingabe, nie eine Prüfung.
13. **Kurze Antworten mit Folgeschritt.** `server/guide.py` verdichtet den Bericht eines Bauschritts auf die nicht bestandenen Prüfungen und höchstens fünf Zeilen `next`. Kleine Modelle folgen damit dem Ablauf, ohne den vollen Bericht zu lesen.

### 4.4 Komponenten

Pfade gelten relativ zu `d2c/`. Nur `drawing/vector/__init__.py` stellt eine öffentliche API bereit (`extract`, `verify`, `suggest_scale`, `STANDARD_SCALES`). Die `__main__.py` eines Unterpakets ist dessen Kommandozeile (`python -m d2c.<paket>`).

| Modul | Aufgabe |
| --- | --- |
| `__init__.py` | Paketbeschreibung mit Schichtübersicht |
| `drawing/__init__.py`, `cad/__init__.py`, `quality/__init__.py`, `server/__init__.py`, `agent/__init__.py`, `web/__init__.py` | Docstring mit der Verantwortung des Unterpakets, ohne Importe |
| `drawing/vector/__init__.py` | Öffentliche API der Vektorauswertung |
| `config.py` | Alle `D2C_*`-Umgebungsvariablen als Funktionen, bei jedem Aufruf gelesen (13.1); Ergebnisordner `runs/` und `versions/` |
| `log.py` | Logging-Einrichtung für die Einstiegspunkte; Protokolle auf stderr |
| `spec.py` | `PartSpec` mit Konsistenz- und Prüfvermerkregeln, `PART_NAME` und die Abnahmeschwellen `LINEAR_TOL`, `AXIS_COS`, `VOLUME_REL_TOL` (7) |
| `oppath.py` | `get_path` und `set_path`: Punktpfade wie `profile.w` in serialisierten Operationen |
| `errors.py` | Verständliche Fehlertexte aus pydantic-Fehlern (`error_report`, `validation_error_message`) |
| `session.py` | Teile, Operationslisten, Aufbau im Worker, Prüfliste, Export (`export_step`) und Persistenz unter `results/`; frühere Versionen eines Teils |
| `drawing/pages.py` | Pfadprüfung unterhalb von `D2C_DATA`, Seiteninformation, PDF-Seite oder Ausschnitt als PNG |
| `drawing/text.py` | Textebene lesen, Maßtexte mit Toleranz, Passung und Symbolen zerlegen |
| `drawing/scale.py` | Maßstab des Schriftfelds lesen und gegen die Geometrie prüfen |
| `drawing/vector/primitives.py` | Linie, Bogen, Kreis, Kurve, Rahmenhilfen und Einheiten der Vektorauswertung |
| `drawing/vector/chains.py` | Seitengeometrie einsammeln und zu Ketten verbinden |
| `drawing/vector/fit.py` | Geraden, Bögen und Kreise anpassen und zusammenführen |
| `drawing/vector/contours.py` | Konturen und Strichklassen der sichtbaren Kanten |
| `drawing/vector/views.py` | `extract`: Kreise, Bögen und Ausdehnung einer Ansicht in Modell-mm |
| `drawing/vector/match.py` | `verify` und `suggest_scale`: gedruckte Maße und Maßstab mit der Geometrie abgleichen |
| `cad/ops.py` | Operationsmodelle und Aufbau des Körpers mit CadQuery (`build`) |
| `cad/checks.py` | Volumenkörper, Maße und Bohrungen prüfen, Metriken |
| `cad/export.py` | STEP AP242 mit semantischem PMI, GLB, PMI-Leser, STEP-Rückimport; schreibt die Dateien von `export_step` |
| `cad/sandbox.py` | Skriptprüfung auf dem Syntaxbaum, Worker-Prozess mit Zeitlimit und Ressourcengrenzen |
| `cad/worker.py` | Einstieg des Worker-Prozesses: baut, prüft und exportiert isoliert |
| `quality/tolerances.py` | ISO 286, ISO 2768, Toleranzmitte, Einhaltung (10) |
| `quality/iso_tables.py` | Eingebaute Normtabellen ISO 2768-1 und ISO 286-1 für den Fall, dass `D2C_TABLES` keine Datei liefert |
| `quality/crosscheck.py` | Plausibilitätsprüfung der `PartSpec` und Vergleich mit einer zweiten Auswertung (`check_spec`) |
| `quality/gates.py` | Gates des Servers: Auslassungen, nicht gebaute Kreise, ungeklärte Abweichungen von `measure_drawing`, unerklärte Maße und Zustand der Lesegates (6.6) |
| `quality/review.py` | Prüfliste als Markdown und JSON (9) |
| `quality/revision.py` | Änderungsliste zweier Revisionen derselben Spezifikation (`diff`) (8.3) |
| `server/__main__.py` | Kommandozeile des MCP-Servers: stdio oder `--http HOST:PORT` |
| `server/tools.py` | Die 15 MCP-Werkzeuge, Fehlerbehandlung, Werkzeuge der Operationen aus den Modellen; Schranken vor `submit_spec` (Messpflicht für angenommene Maße, Maßstabsprüfung, Lesegates) |
| `server/readgate.py` | Lesegates von `submit_spec` (6.6) |
| `server/coerce.py` | Eingabeschicht: deutet Formfehler der Werkzeugargumente und der Spezifikation um und meldet jede Umdeutung (6.3) |
| `server/guide.py` | Kompakte Aufbauberichte und Folgeschritte (`next`) (6.4) |
| `server/instructions.md` | Arbeitsanleitung für das Modell, vom Server an den Client geliefert (länger als 4096 Zeichen: der Agent setzt `CLAUDE_CODE_MAX_MCP_DESCRIPTION_LENGTH=16000`) |
| `agent/__main__.py` | Kommandozeile des Agenten (4.6) |
| `agent/backend.py` | Modell-Backend: Umgebung für Claude, OpenRouter und Ollama, Serverkonfiguration |
| `agent/runner.py` | `run_agent`: Phasen Lesen und Aufbau, Revisionslauf, Ereignisse, Abbruchregeln |
| `agent/batch.py` | Stapellauf: `python -m d2c.agent.batch ORDNER`, mehrere Zeichnungen gleichzeitig, Übersicht als CSV und JSON, Wiederaufnahme |
| `agent/trace.py` | Ereignisprotokoll je Lauf unter `results/<teil>/runs/` |
| `web/app.py`, `web/static/` | Ergebnisansicht im Browser, Hochladen, Läufe per Server-Sent Events (12) |
| `web/__main__.py` | Kommandozeile der Web-Oberfläche |
| `demo/__main__.py`, `demo/__init__.py` | Demo-Lauf ohne Sprachmodell; `DATA` nennt den Ordner der Demo-Daten |
| `demo/demo_drawing.pdf`, `demo/demo_spec.json`, `demo/demo_ops.json`, `demo/tables/` | Demo-Zeichnung, Spezifikation, Operationen und Auszüge der ISO-Tabellen |
| `scripts/` (Repo-Wurzel) | `replay.py` (Teile erneut aufbauen), `eval_refs.py` (Referenzvergleich), `freecad_check.py` (Prüfung in FreeCAD), `bench_models.py` (Messreihe über Modelle), `check_example.py` (Beispielergebnis `examples/demo` gegen einen frischen Demo-Lauf), `make_demo_drawing.py` (Demo-Zeichnung) |
| `examples/demo/` (Repo-Wurzel) | Eingechecktes Beispielergebnis der Demo: STEP, GLB, Prüfliste, Berichte; die Zeichnung liegt unter `d2c/demo/` |

**Schichtregel.** Ein Modul importiert aus d2c nur, was die Tabelle für seine Einheit erlaubt. `tests/test_layers.py` prüft die Regel. `cadquery` und `OCP` werden nur in `cad/` importiert.

| Einheit | Darf aus d2c importieren |
| --- | --- |
| Kern: `config`, `log`, `spec`, `oppath`, `errors` | nur den Kern; von Fremdpaketen nur die Standardbibliothek und pydantic |
| `drawing/` | Kern und `drawing/` |
| `cad/` | Kern und `cad/` |
| `quality/` | Kern und `quality/` |
| `session` | Kern, `cad/` und `quality/` |
| `server/` | Kern, `drawing/`, `cad/`, `quality/`, `session` und `server/` |
| `agent/` | Kern und `agent/`; der Server läuft nur als Unterprozess |
| `web/` | Kern, `drawing/`, `agent/` und `web/` |
| `demo/` | Kern, `quality/`, `session` und `demo/` |

### 4.5 Technologie

Python 3.12, `cadquery==2.8.0` (OCP 7.9.3), `claude-agent-sdk==0.2.168`, `mcp==2.3.0`, pydantic 2, `pymupdf==1.28.2` (Lizenz AGPL), poppler-utils, pytest, Ruff. Docker-Basis `python:3.12-slim`, per Digest festgelegt, mit den Paketen `libgl1`, `libglu1-mesa`, `libxrender1` und `poppler-utils`. Die Python-Abhängigkeiten stammen aus `requirements.lock`, die aus `uv.lock` exportiert wird. Die CI (GitHub Actions, `ubuntu-24.04` und `ubuntu-24.04-arm`) führt Ruff (Prüfung und Format) aus, prüft `requirements.lock` gegen `uv.lock`, baut das Image und führt Tests und Demo mit `--network none` aus. Lizenz des Projekts: AGPL-3.0 oder später (wegen PyMuPDF); die Lizenzen der Drittkomponenten stehen in `NOTICE`.

### 4.6 Agent

`d2c.agent` ist Teil des Pakets. `run_agent` (`agent/runner.py`) steuert den MCP-Server mit einem Modell; das Modell-Backend (Claude, OpenRouter oder Ollama) bestimmt `agent/backend.py`. `python -m d2c.agent` ruft den Lauf von der Kommandozeile auf (Optionen: `--part`, `--model`, `--build-model`, `--base-url`, `--build-base-url`, `--lite` und `--no-lite`, `--server`, `--spec`, `--revise`, `--no-script`, `--max-turns`, `--max-usd`, `--max-minutes`, `--events`); `python -m d2c.agent.batch ORDNER --jobs N` verarbeitet jede PDF eines Ordners als eigenes Teil (Übersicht `batch.csv` und `batch.json` im Ergebnisordner, Wiederaufnahme überspringt Teile mit gültigem `report.json`). Das Paket läuft auf dem Claude Agent SDK, das Claude Code als Agentenschleife startet. Jede Sitzung sieht nur die MCP-Werkzeuge ihrer Phase, keine eingebauten Werkzeuge, keine Einstellungen, Hooks, Plugins und keinen anderen MCP-Server.

- **Phasen:** Ein Modell kann beide Phasen erledigen (`read`, dann `build` in einer Sitzung). Bei anderem `build_model` oder `build_base_url` liest eine erste Sitzung die Zeichnung und reicht die Spezifikation ein, eine zweite baut das Teil daraus und sieht nie ein Bild. Die Lesesitzung nutzt `base_url`, die Bausitzung `build_base_url`: So liest ein Cloud-Modell und ein lokales Modell baut.
- **Revision:** `revise` stellt ein vorhandenes Teil auf eine neue Zeichnung um (8.3); die Lese- und Bauwerkzeuge stehen in einer Sitzung bereit, Ende ist wie sonst der Nennexport, danach darf noch die Toleranzmitte folgen. `done` enthält dann `changes`.
- **Vorgegebene Spezifikation:** `spec` überspringt das Lesen. Der Agent legt die Spezifikation samt leerer Operationsliste im Ergebnisordner ab, wo der Server sie lädt; das Modell muss sie nicht wörtlich zurückschicken. Die Bausitzung erhält zusätzlich `render_drawing` und `measure_drawing`, damit sich die Messpflicht von `submit_spec` für angenommene Maße erfüllen lässt.
- **Lite-Modus:** Für ein Modell ohne Claude-Namen an einem eigenen Endpunkt ist er voreingestellt, `--lite` und `--no-lite` überschreiben das. Er verwendet zwei Sitzungen und kleine Werkzeugsätze (Lesen: `render_drawing`, `read_drawing_text`, `measure_drawing`, `submit_spec`; Bauen: `extrude`, `revolve`, `add_holes`, `edge_finish`, `delete_op`, `get_state`, `export_step`), nennt die vollständigen Werkzeugnamen und fügt eine Kurzprozedur je Phase in den Systemprompt ein.
- **Lokale Modelle:** Für ein Modell ohne Claude-Namen setzt der Agent das Kontextfenster (`D2C_LLM_CONTEXT`, Standard 64000) und ein Zeitlimit je Anfrage von 30 Minuten; `D2C_LLM_THINKING` begrenzt die Denk-Token.
- **Abbruch:** Ein Lauf endet nach `max_turns` Zügen je Sitzung, nach drei gleichen fehlgeschlagenen Aufrufen mit gleicher Antwort, nach sechs fehlgeschlagenen Aufrufen mit gleichen Argumenten (auch bei wechselnder Antwort) oder nach dem ersten erfolgreichen Nennexport; danach verweigert ein PreToolUse-Hook der SDK jedes Werkzeug außer `export_step` mit `variant="mid"` und die Sitzung läuft bis zur Ergebnisnachricht weiter, damit `usage` und `cost_usd` stimmen (nach drei verweigerten Aufrufen wird sie abgebrochen, `cost_usd` ist dann `null`, ebenso nach jeder Sitzung ohne Ergebnisnachricht; in `batch.csv` bleibt die Zelle leer). Als Fehlschlag zählen Werkzeugfehler und Ergebnisse mit `error`; ein Aufbau mit `ok = false` ohne `error` hat nur die Prüfungen noch nicht erfüllt, was mitten im Aufbau normal ist.
- **Grenzen:** `max_usd` (Standard 3, `D2C_MAX_USD`) und `max_minutes` (Standard 20, `D2C_MAX_MINUTES`) begrenzen die Kosten und die Gesamtdauer über alle Sitzungen, 0 hebt die Grenze auf. Wird eine erreicht, endet der Lauf mit `ok = false`, einem `reason` und `limit` = `budget` oder `time`.
- **Ereignisse:** `phase`, `tool_call`, `tool_result`, `message`, `stderr` (die letzten 20 Zeilen der CLI-Fehlerausgabe, wenn der Prozess mit Fehler endet) und `done` mit `ok`, `part`, `files` (nach einem Export mit `variant="mid"` zusätzlich `step_mid` und `report_mid`) oder `reason` (und `limit`), `turns`, `usage` und `cost_usd`. Der Rückkehrcode der Kommandozeile ist 0 nur nach einem Export.

## 5. Datenmodell

### 5.1 PartSpec

Die `PartSpec` beschreibt ein Teil so, wie es auf der Zeichnung steht, bevor Geometrie entsteht.

```
PartSpec
  name: str                  Muster [\w-]+, wird Ordnername unter results/
  drawing: str               Pfad relativ zu D2C_DATA, z. B. "Teil/Teil.pdf"
  page: int = 1
  title: str
  scale: str                 wie im Schriftfeld, z. B. "2:1"
  units: "mm"
  general_tolerance: str | null     "ISO 2768-f", "-m", "-c" oder "-v"
  views: [View]
  dimensions: [Dimension]
  features: [Feature]
  notes: [Note] = []
  datums: [Datum] = []
  geometric_tolerances: [GeometricTolerance] = []
  expected: Expected
  review: [ReviewItem]

View          id, kind (front, top, side, section, detail, iso), scale, note = ""
Dimension     id, kind (length, diameter, radius, angle, chamfer, thread, count),
              value (mm, Winkel in Grad), text (wie gedruckt), source: Source | null,
              confidence (0 bis 1), assumed = false, assumption_basis: str | null,
              tol_upper, tol_lower: float | null, fit: str | null (z. B. "H7"),
              faces_near: [(x, y, z)] = [] (bei Längen je ein Punkt auf beiden bemaßten Flächen)
Source        view, cell: str | null (Rasterfeld A1 bis F8, null ohne Raster)
Feature       id, type (base, boss, pocket, hole, slot, fillet, chamfer, thread, revolve, other),
              dim_ids, note = ""
Note          id, text, source: Source | null
Expected      bbox: (x, y, z) Ausdehnung in Modellkoordinaten, holes: [ExpectedHole] = []
ExpectedHole  d, at: (x, y, z) Punkt auf der Achse, axis: (x, y, z), dim_ids
ReviewItem    id, refs, issue (missing, ambiguous, low_confidence, conflict, convention),
              assumption, reason, mark_near: [(x, y, z)] = []
Datum         id, label (Buchstabe am Bezugsdreieck), near: (x, y, z) auf der Bezugsfläche, source: Source | null
GeometricTolerance
              id, kind (flatness, straightness, roundness, cylindricity, parallelism, perpendicularity,
              angularity, concentricity, symmetry, circular_runout, total_runout, position, line_profile,
              surface_profile), value (mm), text (Rahmen wie gedruckt), datums: [Buchstabe] = [],
              near: (x, y, z) auf der tolerierten Fläche, source: Source | null, confidence
```

Regeln, geprüft beim Einreichen:

- Unbekannte Felder sowie NaN und Unendlich werden abgelehnt.
- Alle IDs sind über Ansichten, Maße, Konstruktionsmerkmale, Hinweise und Prüfeinträge hinweg eindeutig.
- Verweise müssen gültig sein: `refs` auf Maße, Konstruktionsmerkmale oder Hinweise; `dim_ids` auf Maße; `source.view` auf Ansichten.
- **Prüfvermerkpflicht:** Jedes Maß mit `assumed = true` oder `confidence < 0.8` steht in den `refs` mindestens eines Prüfeintrags.
- Ein angenommenes Maß braucht eine nicht leere `assumption_basis`, jeder Prüfeintrag eine nicht leere `assumption` und `reason`.
- Bei nicht angenommenen Maßen enthält `text` eine Zahl gleich `value`. Das Komma gilt als Dezimaltrenner: „76,2“ ergibt 76,2, nie 762.
- Form- und Lagetoleranzen: Bezugsbuchstaben sind eindeutig und vorhanden; Formtoleranzen haben keinen Bezug, Richtungs-, Lage- und Lauftoleranzen mindestens einen; `text` enthält `value`; eine Lesung mit `confidence < 0.8` steht in einem Prüfeintrag.
- `tol_upper` und `tol_lower` werden nur gemeinsam angegeben, `tol_lower` liegt nicht über `tol_upper`.
- Ein Konstruktionsmerkmal vom Typ `hole` verlangt mindestens einen Eintrag in `expected.holes`. Eine Achse darf nicht der Nullvektor sein.
- `mark_near` ist eine Punktliste; eine flache Dreierliste gilt als ein einzelner Punkt und wird verpackt, andere Formen werden mit der richtigen Form in der Meldung abgelehnt.
- Ein Maßstab wie `1/2` wird zu `1:2` normalisiert; `PartSpec.scale` und `View.scale` tragen immer das Doppelpunktformat, wenn sich ein Verhältnis erkennen lässt.
- Alle Verstöße kommen gesammelt in einer Fehlermeldung zurück.

### 5.2 Operationen

| `op` | Felder | Wirkung |
| --- | --- | --- |
| `extrude` | `profile`, `plane`, `distance` (nicht 0), `mode`, `both`, `taper` | Profil aus der Ebene extrudieren; `both` symmetrisch zur Ebene; `taper` als Entformungsschräge in Grad |
| `revolve` | `points` (r, z), `profile` (path) oder `steps` (abgesetzte Welle als [Ø, Länge] von z=0 an), `angle` (bis 360 Grad), `mode` | Profil in der XZ-Ebene um die globale Z-Achse drehen |
| `holes` | `plane`, `d`, `positions` oder `polar`, `depth`, `kind`, `cbore_d`, `cbore_depth`, `csk_d`, `csk_angle`, `thread`, `thread_core`, `flip` | Bohrungen abtragen |
| `edge_finish` | `kind` (fillet, chamfer), `size`, `near` oder `on_plane`, `tol` (Standard 0,5 mm) | Rundung oder Fase an allen Kanten nahe den Punkten oder an allen Kanten, die ganz in der Ebene `on_plane` liegen |
| `script` | `code` | Rückfall: CadQuery-Code ersetzt den Körper durch `result` |

- **Verknüpfung:** `extrude` und `revolve` haben `mode`: `add` vereinigt, `cut` zieht ab, `intersect` schneidet mit dem bisherigen Körper. Die erste Operation muss Material aufbauen (`add` oder `script`). `holes` trägt immer ab, `edge_finish` ändert den bestehenden Körper.
- **Profile:** `rect` {center, w, h, corner_radius}, `circle` {center, d}, `slot` {center, center_distance, width, angle}, `path` {start, segments aus `line`, `arc3` (optional mit `r`: `via` wird auf den Kreis mit dem gedruckten Radius projiziert) und `radius_arc`, fillets mit `at` und `r`}, `hull` {circles aus (u, v, r)}. `hull` erzeugt die exakte Kontur aus Kreisbögen und äußeren Tangenten.
- **Ebenen:** `XY` mit Normale +Z (u = X, v = Y), `XZ` mit Normale −Y (u = X, v = Z), `YZ` mit Normale +X (u = Y, v = Z), `custom` mit `origin`, `normal` und `xdir`. `offset` verschiebt entlang der Normalen. Profil- und Bohrungskoordinaten (u, v) gelten in der Ebene, `near` und Skripte global. Einheiten mm und Grad, Z nach oben.
- **Bohrungen:** Sie beginnen in der Ebene und laufen gegen die Normale, `flip` kehrt die Richtung um. Ohne `depth` gehen sie durch das ganze Teil. `positions` listet die Mittelpunkte, `polar` {center, pitch_d, count, start_angle} verteilt sie auf einem Lochkreis. `cbore` ergänzt eine Flachsenkung (`cbore_d`, `cbore_depth`), `csk` eine Kegelsenkung (`csk_d`, Spitzenwinkel `csk_angle`, Standard 90 Grad).
- **Gewinde:** `kind = tapped` mit `thread` wie „M5“ oder „M10x1“ und `d` gleich dem Nenndurchmesser. Modelliert wird die Kernlochbohrung ohne Gewindegeometrie: Standard ist `thread_core = tap_drill`, die Kernlochbohrung, wie sie die Bohrungsfeatures der meisten CAD-Systeme modellieren, nach Tabelle (M2 1,6 · M2,5 2,05 · M3 2,5 · M4 3,3 · M5 4,2 · M6 5,0 · M8 6,8 · M10 8,5 · M12 10,2, sonst Nenndurchmesser minus Steigung); mit `minor` der Kerndurchmesser d − 1,082532 · P (M3 2,459 · M5 4,134) für Teile, die so modelliert sind. Regelsteigungen sind für M2, M2,5, M3, M4, M5, M6, M8, M10, M12, M16 und M20 hinterlegt; für andere Größen gehört die Steigung in die Angabe, z. B. „M14x2“.
- **Kantenauswahl:** Gewählt wird jede Kante außer Nahtkanten, die höchstens `tol` von einem der Punkte `near` entfernt ist oder (mit `on_plane`) ganz in der Ebene liegt. Ohne Treffer meldet die Operation einen Fehler.
- **Rückverfolgbarkeit:** Jede Operation trägt `refs: {"<Parameterpfad>": "<Maß-id>"}`, z. B. `{"profile.d": "d1"}`. Der Pfad muss auf eine Zahl der Operation zeigen, die id muss in der `PartSpec` stehen.
- **Prüfungen beim Aufbau:** Eine Entformungsschräge, die das Profil vor der vollen Länge zusammenfallen lässt, ist ein Fehler. Ein Rotationsprofil darf die Achse nicht überschreiten. Eine Rundung im Profil braucht genau eine Ecke im Umkreis von 0,5 mm um `at`. Entfernt ein abtragender Schritt kein Material, meldet der Modellaufbau die Warnung `Kein Material entfernt`; beim Hinzufügen oder Ändern einer Bohrung oder eines abtragenden Extrudierens kehrt der Server die Richtung dann selbst um (6.5).

### 5.3 BuildReport

Der Modellaufbau erzeugt diesen Bericht; `export_step` gibt ihn vollständig zurück, die Bauwerkzeuge nur in der kompakten Form von 5.4 (mit `D2C_REPORT=full` ebenfalls vollständig):

```
{ "ok": bool, "error": str | null,
  "solid": {"valid", "solids", "free_edges", "volume", "bbox": [xmin, ymin, zmin, xmax, ymax, zmax], "cyl_d", "circle_d", "ok"},
  "dims": [{"check": "bbox_x" | "bbox_y" | "bbox_z" | "hole", "expected", "actual", "ok", "dim_ids",
            "through": true | false | null, "entry": ...}],   // through und entry nur bei Bohrungen
  "dims_found": {"found": [{"id", "text", "value", "nearest"}], "missing": [{"id", "text", "value"}]} | null,
  "build_notes": [{"op_index", ...}],
  "roundtrip": {"path", "reimport_valid", "solids", "volume_rel_diff", "marks": [{"id", "faces"}],
                "pmi": {"written", "read_back", "assumed", "missing"}, "ok"},
  "op_index": int }
```

- `ok` ist wahr, wenn `solid.ok`, alle `dims` und `roundtrip.ok` wahr sind.
- Bei `error` (ungültige Operation, Fehler im Aufbau, Zeitüberschreitung) bleibt die bisherige Operationsliste unverändert. Ein Ergebnis mit `ok = false` ohne `error` wird übernommen, denn während des Aufbaus fehlen Konstruktionsmerkmale noch.
- `op_index` ist der Index der neuen oder geänderten Operation. Der Bericht von `export_step` hat kein `op_index`.
- `dims` zeigt je Bohrung in `through`, ob sie durchgeht (`true`, `false` oder `null`, wenn keine Bohrung zum Eintrag passt), und in `entry`, ob sie von außen erreichbar ist. Ein Sackloch ist erlaubt; ein geschlossener Hohlraum ohne Zugang setzt `ok = false`.
- `dims_found` sucht jedes gedruckte Ø-, R- und Längenmaß der `PartSpec` im Körper (Toleranz 0,05 mm; angenommene Maße, Winkel, Fasen, Gewinde und Zählmaße nicht). Kandidaten sind Radien, Durchmesser, Kantenlängen, Ausdehnungen des Hüllquaders, Abstände paralleler Ebenen und Lagemaße der Zylinderachsen: Abstand einer Achse von einer parallelen Ebene, Abstand zweier paralleler Achsen und dessen Anteile in X, Y und Z (`checks.position_distances`; Achspaare nur bis `MAX_AXES` = 200 verschiedene Achsen). `missing` nennt die Maße, die im Körper nicht vorkommen; sie machen den Aufbau nicht ungültig, stehen aber in den Hinweisen des Aufbaus und in der Prüfliste (9). Die Prüfungen belegen damit Konsistenz mit der erfassten Spezifikation, nicht mit der Zeichnung (7).
- `build_notes` enthalten je Operation Zusatzangaben, z. B. `drilled_d`, `edges_selected`, `hull_circles` oder `warning`. Der Bericht listet außerdem die Zylinder des Körpers (`solid.cyl_d`) und alle Durchmesser runder Merkmale (`solid.circle_d`, auch Kegelränder und Kreiskanten), und `coerced` führt Notizen zur automatischen Richtungsumkehr.

### 5.4 Antwort der Bauwerkzeuge

`extrude`, `revolve`, `add_holes`, `edge_finish`, `run_script`, `edit_op`, `delete_op` und `move_op` antworten mit dem kompakten Bericht von `server/guide.py`:

```
{ "ok": bool, "error": str | null, "op_index": int,
  "coerced": [str],            nur wenn der Server Argumente umgedeutet hat (6.3, 6.5)
  "failed": [str],             nicht bestandene Prüfungen im Klartext, z. B. "bbox_x: soll 80, ist 60"
  "passed": int, "next": [str], "notes": [str], "volume": float | null, "bbox": [6 Werte] | null }
```

- `failed` nennt je Maß Soll und Ist und je fehlender Bohrung Durchmesser, Lage, Achse und, falls vorhanden, den nächsten Zylinder des Körpers.
- `next` hat höchstens fünf Zeilen in der Reihenfolge der Dringlichkeit: Fehler beheben (ein abgelehnter Vorgang ändert die Liste nicht), ein zweiter, getrennter Körper, Bohrungen außerhalb des Körpers (dann ist der Grundkörper falsch platziert), fehlende Bohrungen als fertiger `add_holes`-Aufruf (je Durchmesser und Achse gruppiert), gedruckte Durchmesser, die im Körper fehlen, Maße ohne `refs`, und zuletzt `export_step`, wenn alle Prüfungen bestehen. Dasselbe Feld liefert `submit_spec` als ersten Schritt.
- `notes` sind die Warnungen des Aufbaus mit Index der Operation.

## 6. MCP-Schnittstelle

Der Server heißt `drawing2cad` und liefert `instructions.md` als Arbeitsanleitung an den Client. Er bietet 15 Werkzeuge, keine Ressourcen und keine Prompts. Transport ist stdio; mit `python -m d2c.server --http HOST:PORT` spricht er Streamable HTTP unter `/mcp`. Vor jedem Werkzeug läuft die Eingabeschicht (6.3), jedes Bauwerkzeug antwortet in der Form von 5.4.

### 6.1 Werkzeuge

| Werkzeug | Parameter | Ergebnis |
| --- | --- | --- |
| `render_drawing` | `pdf`, `page` = 1, `dpi` = 100, `crop` | PNG der Seite oder des Ausschnitts und Seiteninformation |
| `read_drawing_text` | `pdf`, `page` = 1, `region` | `items` (Textelemente mit Position, Schrift und zerlegtem Maß, höchstens 300 je Aufruf), `printed_dims` (die verschiedenen gedruckten Nennwerte ohne Abmaße und Gewinde) und `title_scale` (Maßstab des Schriftfelds wie „2:1“ oder null) |
| `measure_drawing` | `pdf`, `page` = 1, `region`, `scale`, `printed_dims`, `expect`, `edge_width` = "auto" | Ausdehnung, je höchstens 12 Kreise und Bögen und die 40 längsten Geraden (`lines`) in mm, `circle_groups` (alle Kreise je Durchmesser mit Anzahl), Maßstabsvorschlag, Prüfung von Angaben wie `d=30` oder `L=80`, Hinweise |
| `check_spec` | `spec`, `second` | Befunde zu unmöglichen Werten: Bohrung außerhalb des erwarteten Rahmens, überlappende Bohrungen, zu große Rundung, und mit `second` jeder Unterschied zu einer zweiten unabhängigen Auswertung derselben Seite; es wird nichts übernommen |
| `submit_spec` | `spec`, `keep_ops` = false | Teil anlegen oder neu beginnen; Ergebnis mit `scale_check` und `next`; mit `keep_ops=true` bleiben die Operationen erhalten und der Aufbau-Bericht steht unter `report`; bei einem schon exportierten Teil zusätzlich `changes` und `previous_version` (8.3) |
| `extrude`, `revolve`, `add_holes`, `edge_finish` | `part` und die Felder der Operation | kompakter Bericht (5.4) |
| `run_script` | `part`, `code` | kompakter Bericht |
| `edit_op` | `part`, `index`, `op` | kompakter Bericht |
| `delete_op` | `part`, `index` | kompakter Bericht |
| `move_op` | `part`, `index`, `new_index` | kompakter Bericht |
| `get_state` | `part` | Kurzfassung der Spec, Operationen, letzter Bericht |
| `export_step` | `part`, `variant` = "nominal" | Bei Erfolg Dateien und Bericht, sonst `ok = false` mit Bericht; `variant="mid"` baut auf den Toleranzmitten und schreibt nur `<teil>_mid.step` und `report_mid.json` |

- `crop` und `region` sind Bruchteile der angezeigten Seite (x0, y0, x1, y1), Ursprung oben links.
- `render_drawing` begrenzt die Auflösung auf 600 dpi und die längere Bildseite auf 2000 Pixel.
- `measure_drawing` misst in Millimetern im Maßstab `scale` (ohne Angabe 1:1). Jede Angabe in `expect` erhält den Status `ok` (höchstens 0,05 mm Abweichung), `MISMATCH` oder `no_candidates`. Die Art einer Angabe ist `d` oder `Ø` und `D` (Durchmesser), `r` oder `R` (Radius), `L` oder die Kürzel `W`, `B`, `H`, `T` und `A` (Länge). Der Maßstab darf als `1/2` oder `1:2` angegeben werden. Aus `printed_dims` (empfohlen sind mindestens sechs) schlägt es einen Maßstab vor und meldet, wenn der Maßstab des Schriftfelds nicht zur Geometrie passt.
- `edge_width` legt fest, welche Strichstärke als sichtbare Kante zählt: `auto` erkennt sie, `all` nimmt alle, eine Zahl wählt die nächstliegende Strichstärke in pt.
- `submit_spec` für ein vorhandenes Teil beginnt neu: Die Operationsliste wird geleert. Hatte das Teil schon ein STEP, wandert der bisherige Stand (außer Laufprotokollen und früheren Versionen) vorher nach `results/<teil>/versions/<Zeitstempel>/`. Mit `keep_ops = true` bleibt alles an Ort und Stelle; ein `_mid.step`, das nicht mehr zu den Operationen passt, wird entfernt. Ein Maß, das zuvor `assumed = true` war, wechselt nur nach einer neuen Messung (`measure_drawing`) zu `assumed = false`. Das Ergebnis enthält `scale_check` und `warnings` (Befunde der Plausibilitätsregeln wie in `check_spec`; sie lehnen nichts ab).
- Ungültige Parameter, unbekannte Teile und Indizes außerhalb der Liste gehen als `ToolError` mit verständlicher Meldung an den Client; andere Ausnahmen werden als allgemeiner Werkzeugfehler ausgegeben. Eine Fehlermeldung nennt Feldpfad, Fehlerart, den erhaltenen Wert und, wo es hilft, ein Beispiel, außerdem die Umdeutungen der Eingabeschicht. Fehler beim Modellaufbau stehen im Feld `error` des Berichts.

### 6.2 Zeichnungsanalyse

- **Rendern** (`drawing/pages.py`): PyMuPDF rendert die angezeigte Seite, also die CropBox mit der Seitendrehung; scheitert es an einer Datei, ist poppler `pdftoppm` der optionale Rückfall. Seiten zählen ab 1.
- **Textebene** (`drawing/text.py`): poppler `pdftohtml -xml` liefert Textstücke, die zu Zeilen zusammengesetzt werden. Zeichen der Symbolschriften `ISO1Symbols` und `GDTSymbols` haben keine Unicode-Zuordnung und werden zurückübersetzt: Ø, ±, ⊥, ⌖, ∥, ▱, ◎, ⌯, ↗ und Ⓔ (Hüllbedingung). Jedes Maß wird zerlegt in `prefix` (Ø, R, M), `count` (z. B. 4x), `nominal`, `tol_upper`, `tol_lower`, `fit`, `envelope` und `thread`. Eine gestapelte Toleranz bleibt ein eigenes Element neben ihrem Maß. Rahmen von Form- und Lagetoleranzen werden nicht zerlegt. Schräger Text kann zerstückelt sein und wird im Bild geprüft. Ist der Text einer Zeichnung in Linien umgewandelt, liefert die Textebene keine Elemente; das Werkzeug meldet dann, dass die Maße im gerenderten Bild zu lesen sind. `printed_dims` und `title_scale` des Werkzeugs `read_drawing_text` sind die Eingabe für `measure_drawing` (`printed_dims`, `scale`).
- **Vektormessung** (`drawing/vector/`, PyMuPDF): Kreise und Bögen entstehen aus Bézierkurven und Polylinien der sichtbaren Kanten. Die Ausdehnung ist das Hüllrechteck der größten geschlossenen Kontur, ohne Kontur das aller sichtbaren Elemente. Ergebnisse stehen in Modell-mm (Blatt-mm geteilt durch den Maßstab), x nach rechts, y nach oben, Ursprung unten links im Bereich. Der Maßstabsvorschlag (`drawing/vector/match.py`) vergleicht gemessene mit gedruckten Maßen und liefert bis zu fünf Verhältnisse, geordnet nach der Zahl der Übereinstimmungen, jeweils mit dem passenden Normmaßstab (1:20 bis 20:1, Abweichung unter 0,5 %), falls es einen gibt.

### 6.3 Eingabeschicht (`server/coerce.py`)

Der Server deutet Formfehler der Argumente um, bevor das Schema sie prüft. Gültige Eingaben bleiben unverändert; jede Umdeutung steht als Notiz `pfad: alt → neu (Grund)` unter `coerced` in der Antwort, und eine abgelehnte Eingabe nennt sie in der Fehlermeldung. Umgedeutet wird nur, was sonst abgelehnt oder still verworfen würde:

- Ein Text, der JSON enthält, wird gelesen; eine Liste als Text wird an Leerzeichen, Semikolon und Komma geteilt.
- Zahlenfelder mit Text („6,6“, „90°“, „Ø16H7“) werden zu Zahlen. Freitext, Bezeichner und Pfade werden nie umgedeutet.
- Flache und gemischte Punktlisten (`positions`, `points`, `steps`, `near`, `mark_near`) werden zu Punkten gruppiert; ein Einzelpunkt wird zur Liste. Ausnahme: `near` eines Bezugs oder einer Form- und Lagetoleranz ist ein einzelner Punkt; eine Liste mit genau einem Punkt wird dort entpackt.
- Aliase: `points` und `diameter` bei `add_holes`, `height` und `thickness` bei `extrude`, `radius` bei `edge_finish`, Modus `subtract` oder `remove` für `cut`, `union` oder `join` für `add`, Profil `rectangle` für `rect` mit `width` und `height`, Kreisprofil mit `diameter` oder `radius`, Ebene als Text („xy“) für ein Ebenenobjekt, `through: true` und `depth: 0` bei Bohrungen als Durchgangsbohrung.
- Eine Bohrung ohne Ebene beginnt an der Oberseite des bisherigen Teils.
- Vertauschte `refs` (`{"d6": "76.2"}` statt `{"distance": "d6"}`) werden über den Wert dem Pfad zugeordnet; ohne eindeutigen Pfad wird der Eintrag entfernt und gemeldet.
- Der Maßstab von `measure_drawing` als Text („2:1“, „0,5“) wird zur Zahl.
- In der `PartSpec`: Wörter für Problemart, Maßart und Ansichtsart, Quelle als Text („v1 B3“), Allgemeintoleranz als Text („ISO 2768-m“), Achsen als Buchstabe und Verweise in Prüfeinträgen.
- Unbekannte Argumente würden still verworfen; stattdessen werden sie gemeldet und entfernt.

Die Schicht ändert nie die Schwellen der Prüfungen (7).

### 6.4 Hinweise (`server/guide.py`)

`server/guide.py` erzeugt aus Bericht, `PartSpec` und Operationsliste die Felder `failed` und `next` (5.4). Es sind reine Funktionen auf Wörterbüchern ohne CadQuery. `next` führt das Modell durch den Ablauf: fehlende Bohrungen mit fertigem Aufruf, Maße ohne `refs` in einer Zeile (höchstens sechs), höchstens drei gedruckte Durchmesser oder Radien, die im Körper fehlen.

### 6.5 Automatische Richtungsumkehr

Entfernt eine neue oder geänderte Operation kein Material (`NO_EFFECT`), baut der Server sie einmal mit umgekehrter Richtung: bei Bohrungen mit `flip` umgeschaltet, beim abtragenden Extrudieren ohne `both` mit negiertem `distance`. Liefert die Variante weder Fehler noch Warnung, ersetzt sie die Eingabe, und die Antwort nennt die Änderung unter `coerced`; sonst bleibt die Eingabe mit der Warnung.

Ein Bogen `arc3` eines path-Profils, dessen Radius höchstens 0,02 mm neben einem Radius der Spezifikation liegt (Radiusmaß oder halber Durchmesser), wird vor dem Aufbau auf genau diesen Radius gelegt: `via` rückt auf den Kreis durch Start und Ende, der Mittelpunkt bleibt auf seiner Seite der Sehne (`quality/arcs.py`). Anlass ist ein auf 0,01 mm gerundeter Zwischenpunkt, der den Radius um einige Tausendstel verschiebt. Jede Verschiebung steht unter `coerced`.

### 6.6 Gates

Gates lehnen einen Schritt je Bauteil und Befund genau einmal ab; derselbe Befund geht beim nächsten Versuch mit einer Warnung durch, die in `warnings` und in der Prüfliste steht. Ihre Texte sind englisch und für das Modell bestimmt. Sie erkennen Anzeichen, nie die Geometrie selbst.

- **Lesegates** (`submit_spec`, `server/readgate.py`, Ablehnung als `ToolError`): jede Seite einer mehrseitigen PDF mit `render_drawing` oder `read_drawing_text` gesehen; ein `render_drawing` mit `crop` und wirksam mindestens 200 dpi je Zeichnung; jeder Wert aus `printed_dims` von `measure_drawing` steht in einem Maß; trägt ein Maß weniger als 25 % der Zahlenwerte der Textebene, lehnt es ab, unter 60 % warnt es nur. Gleicht die eingereichte Spezifikation der zuletzt übernommenen des Bauteils (Speicher oder `results/<teil>/spec.json`), entfallen Lesegates und Messpflicht: Das ist die Bausitzung, die die Spezifikation der Lesesitzung in einem neuen Serverprozess erneut einreicht.
- **Soll-Gate** (`submit_spec` mit `keep_ops=true` auf ein Teil mit Ops, `quality/gates.py`, Ablehnung als `ToolError`): Setzt die Einreichung `expected.bbox` oder den Durchmesser einer erwarteten Bohrung auf die Werte des zuletzt gebauten Modells, ohne dass sich ein Maß in `dimensions` ändert, lehnt das Gate einmal ab (die Soll-Werte stammen aus der Zeichnung, nicht aus dem Modell) und lässt den Befund beim zweiten Einreichen mit Warnung in `warnings` und in der Prüfliste durch; ausgenommen sind die Revision eines exportierten Teils, die unveränderte übernommene Spezifikation, Änderungen weg vom Modell und Hüllquaderwerte unter jedem Längen- und Durchmessermaß (Platzhalter).
- **Exportgates** (`export_step` mit `variant="nominal"`, `quality/gates.py`, Ablehnung als `{"ok": false, "error", "next"}` ohne neue Dateien): (1) ein Prüfeintrag (außer zu Gewinden) oder ein Hinweis bekennt ein weggelassenes Merkmal („nicht modelliert“, „vereinfacht“, „weggelassen“, „omitted“ und ähnlich); (2) ein Hinweis nennt Fase, Rundung oder Formschräge, und keine Op kann sie tragen (`edge_finish`, Pfad- oder Rundungsprofil, Drehprofil, `taper`, ein Skript mit dem Stichwort); (3) ein von `measure_drawing` in einer Ansicht mit angegebenem Maßstab gemessener Kreis ab 1 mm, der kein Teilkreis ist (mindestens zwei kleinere Kreise mit dem Mittelpunkt auf ihm), hat im Modell (`solid.circle_d`, ±0,05 mm) kein Gegenstück und steht in keinem Prüfeintrag; (4) ein `MISMATCH` von `expect` betrifft ein Maß, das weder korrigiert noch von einem Prüfeintrag `conflict` genannt ist; (5) ein gedrucktes Maß fehlt im Modell (`dims_found.missing`) ohne Prüfeintrag, der es per `refs` oder mit dem Wert nennt. Die Messungen merkt sich die Sitzung je Zeichnungsseite; eine neue Messung derselben Ansicht ersetzt die frühere.
- **`edit_op`** antwortet bei einer Operation, die der bestehenden gleicht, mit `ok = true` und `unchanged`, ohne neu zu bauen.

## 7. Prüfungen und Abnahmeschwellen

| Prüfung | Schwelle |
| --- | --- |
| Volumenkörper | `isValid()`, genau ein Volumenkörper, keine freien Kanten, Volumen größer 0 |
| Gesamtmaße | Achsparallele Ausdehnung je Achse in Modellkoordinaten höchstens 0,01 mm Abweichung von `expected.bbox` |
| Bohrungen | Eine konkave Zylinderfläche je erwarteter Bohrung: Durchmesser höchstens 0,01 mm Abweichung, Abstand der Achse zu `at` höchstens 0,01 mm, Achsen parallel (Betrag des Kosinus mindestens 0,9999) |
| STEP-Rückimport | Gültiger Rückimport, genau ein Volumenkörper, relative Volumenänderung höchstens 1e-6 (beide Volumen adaptiv integriert, `precise_volume`: die Standardintegration von OCC weicht bei Splineflächen um einige 1e-5 ab), jede Markierung trifft mindestens eine Fläche, alle geschriebenen semantischen PMI-Maße werden im Rückimport wieder gelesen |

Die Schwellen stehen als `LINEAR_TOL` (0,01 mm), `AXIS_COS` (0,9999) und `VOLUME_REL_TOL` (1e-6) in `spec.py`. Die Prüfungen laufen nach jedem Modellaufbau, nicht nur beim Export. Sie vergleichen das Modell mit der erfassten Spezifikation (`expected`), nicht mit der Zeichnung: Das Siegel „All checks passed“ belegt Konsistenz, nicht die richtige Lesung. Ob eine Bohrung durchgeht, wird gemeldet (`through`); Tiefe und Seite einer Bohrung sowie zusätzliche, nicht erwartete Bohrungen werden nicht geprüft.

### 7.1 Unabhängige Prüfung in FreeCAD

`scripts/freecad_check.py <datei>.step [--spec spec.json] [--freecad PFAD] [--tol MM] [--mid]` öffnet eine exportierte STEP-Datei in FreeCAD (`freecadcmd`, nicht Teil des Images) und misst sie ohne CadQuery. Ohne `--spec` prüft es: gültiger Körper, genau ein Volumenkörper, positives Volumen. Mit der `spec.json` des Exports prüft es zusätzlich die Ausdehnung und jede erwartete Bohrung mit den Schwellen aus `spec.py` (Standard 0,01 mm, mit `--tol` änderbar; Achsen parallel ab 0,9999; Bohrungen sind konkave Zylinder). Eine STEP-Datei auf Toleranzmitte weicht vom Nennmaß ab; mit `--mid` ersetzt das Skript die Sollwerte durch die Messwerte des Aufbaus aus `report_mid.json` neben der Datei und fehlt diese, endet es mit Code 1. Rückkehrcode: 0 bestanden, 1 Prüfung nicht bestanden oder Datei nicht lesbar, 2 FreeCAD nicht gefunden. STEP-Leser und Messcode sind unabhängig vom Aufbau, der Geometriekern (OpenCascade) ist derselbe.

## 8. Export

### 8.1 STEP und GLB aus `export_step`

Bei bestandenen Prüfungen schreibt der Modellaufbau die STEP-Datei über `cad/export.py` (8.2): Schema AP242, Einheit mm, mit Farben, Namen, Ebenen und semantischem PMI. Dazu entsteht eine GLB-Datei mit denselben Farben:

| Bedeutung | Farbe (RGB) |
| --- | --- |
| Körper | 0,6 / 0,67 / 0,69 |
| Fläche eines Prüfeintrags, benannt mit dessen ID | 1,0 / 0,5 / 0,0 (orange) |
| Fläche einer Gewindebohrung | 0,396 / 0,941 / 0,451 (grün) |

Eine Fläche gehört zu einem Prüfeintrag, wenn sie höchstens 0,5 mm von einem seiner Punkte `mark_near` entfernt ist. Ausnahme ist ein Eintrag mit angenommenem Maß, für das PMI entsteht: Seine Markierung sind genau die Flächen dieses PMI (bei einer Länge die bemaßte Fläche und ihre Gegenebene, bei Radius und Durchmesser die Zylinderfläche), auch wenn ein Punkt auf einer Kante weitere Flächen träfe. `export_step` schreibt zuerst in einen temporären Ordner im Teileordner und ersetzt die Ergebnisdateien erst, wenn alle Prüfungen bestehen. Bestehen nicht alle, meldet es `ok = false` und lässt das letzte gute STEP und GLB mit ihrer Prüfliste unberührt. Mit `variant="mid"` baut der Export jedes über `refs` gebundene tolerierte Maß auf seiner Toleranzmitte und schreibt nur `<teil>_mid.step` und `report_mid.json`; die Normtabellen liefert `quality/tolerances.py` (10), eingebaute oder aus `D2C_TABLES`. Der Bericht des Exports enthält unter `compliance` je toleriertem Maß die Einhaltung beim Nennmaß und bei der Toleranzmitte, dazu unter `geometry`, ob der Körper ein Maß dieser Größe enthält. Die Antwort von `export_step` nennt zusätzlich `warnings` (zum Beispiel eine Passung ohne Tabelleneintrag), bei `variant="mid"` die verschobenen Werte in `shifts` und die nicht verschiebbaren Bindungen in `skipped`. In `report.roundtrip.pmi` zählen `assumed` die angenommenen Maße, für die PMI entstand, und `missing` jene, für die keines entstand; ein angenommenes Maß ohne PMI setzt `All checks passed` auf no.

### 8.2 Modul `cad/export.py`

`cad/export.py` schreibt die STEP- und GLB-Dateien von `export_step` (8.1) und ist selbst geprüft. Es stellt bereit:

- **STEP AP242** (`AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF`) über den XCAF-Writer von OpenCascade, mit Farben, Namen, Ebenen und semantischem PMI.
- **Semantisches PMI:** Bohrungsdurchmesser als `DIMENSIONAL_SIZE` mit Passung (`LIMITS_AND_FITS`) oder Plus-Minus-Toleranz. Ein angenommenes, fehlendes Längenmaß zwischen zwei parallelen Ebenen als `DIMENSIONAL_LOCATION`, ein angenommener Radius oder Durchmesser als Durchmesser auf der Zylinderfläche, jeweils mit der Beschreibung „ASSUMPTION: <Annahme> - <Begründung> - please check“. Bei einer Länge ist eine von `mark_near` getroffene Ebene (bei einem Punkt auf einer Kante jede der anliegenden) der Anker; gesucht wird die parallele Gegenebene im ganzen Körper im Abstand des Maßes (±0,05 mm). Bei mehreren Paaren gewinnen solche mit entgegengesetzt zeigenden Normalen, dann der kleinste Abstandsfehler, dann die größere Fläche. Liegt die Zylinderfläche schon als Bohrung vor, trägt dieser Eintrag den Hinweis. Ohne Treffer entsteht kein Eintrag, und `roundtrip.pmi.missing` nennt das Maß. Ein Längenmaß mit `faces_near` wird mit seiner Toleranz oder Passung als `DIMENSIONAL_LOCATION` zwischen den Flächen an den beiden Punkten geschrieben, wenn deren Abstand (parallele Ebenen, Bohrungsachse zu Ebene oder zwei Achsen) dem Maß entspricht. Form- und Lagetoleranzen stehen als `GEOMETRIC_TOLERANCE` auf der Fläche am Punkt `near` (höchstens 0,5 mm entfernt), mit `DATUM_FEATURE`, `DATUM` und Bezugssystem; OCCT schreibt ihre Werte in Metern. Was keine Fläche trifft, nennt der Export als Warnung in `build_notes`. Die Einträge erzeugen `pmi_from` und `gdt_from` aus der `PartSpec` und dem Prüfergebnis des Aufbaus.
- **Markierung:** Prüfflächen orange, benannt „CHECK <id>“ (ASCII), auf der Ebene `ASSUMED_CHECK`. Gewindebohrungen grün (0,396 / 0,941 / 0,451). Namen und Beschreibungen werden in ASCII umgesetzt („Ø“ wird „dia“).
- **GLB** für Web-Ansichten, mit denselben Farben (Material wird matt gesetzt), dazu `marks.json` (Farbe zu Prüfeintrags-IDs). PMI und Ebenen gehen im GLB verloren.
- **PMI-Leser** `read_pmi`: Schema, Maße mit Toleranz und Passung, Form- und Lagetoleranzen mit Bezügen, Farben und Ebenen einer STEP-Datei.
- **Messung der Geometrie** `measure_geometry`: Durchmesser aller Zylinderflächen und Abstände aller Paare paralleler Ebenen sowie die Lagemaße der Zylinderachsen aus `checks.position_distances` (Achse zu Ebene, Achse zu Achse mit Anteilen in X, Y und Z); `measure_step` liest sie aus einer STEP-Datei. Damit prüft `quality/tolerances.py` tolerierte Maße ohne `refs`.

### 8.3 Neue Zeichnungsrevision

Ein vorhandenes Teil lässt sich auf eine geänderte Zeichnung umstellen, ohne es neu zu bauen. Der Ablauf: `get_state` liest Spezifikation und Operationen, `submit_spec(keep_ops=true)` übernimmt die neue Spezifikation und baut die Operationen gegen sie neu auf, `edit_op`, `add_holes` und `delete_op` ändern nur die Unterschiede, danach folgen `export_step` und `export_step(variant="mid")`. `python -m d2c.agent --revise` und `run_agent(revise=True)` fahren diesen Ablauf in einer Sitzung mit allen Lese- und Bauwerkzeugen (im Lite-Modus mit den kleinen Sätzen); ohne `results/<teil>/spec.json` und `ops.json` endet der Lauf mit `ok = false` und einem `reason`.

Hat das Teil beim `submit_spec(keep_ops=true)` schon ein exportiertes Modell (`<teil>.step`), gilt der Aufruf als neue Revision. `quality/revision.py` (`diff`) vergleicht dann die alte mit der neuen Spezifikation. Ändert sich etwas Erfasstes, wandert der bisherige Stand nach `versions/<Zeitstempel>/` und die Antwort enthält `changes` und `previous_version`; beides steht in `results/<teil>/changes.json` (`{"previous": <Version>, "changes": [...]}`). Bis zum nächsten Export gilt die Revision als offen: Weitere Einreichungen werden gegen die abgelegte Version verglichen. Ein Neubeginn ohne `keep_ops` löscht `changes.json`.

Ein Eintrag der Änderungsliste hat `area` (`dimension`, `tolerance`, `general_tolerance`, `geometric`, `hole`, `bbox`), `change` (`added`, `removed`, `changed`), `feature` (Art und ID) sowie `before` und `after` als Anzeigetexte. Zuordnung, weil sich IDs zwischen Revisionen ändern: Maße (und Form- und Lagetoleranzen) werden stufenweise eins zu eins gepaart, jede Stufe nur unter den noch freien: gleiche Art und gleicher gedruckter Text (ohne Leerzeichen, „=“ und „mm“, Dezimalkomma gleich Dezimalpunkt), dann gleiche Art und gleicher Wert (nur Maße), dann gleiche Art und gleiche ID. Bohrungen: gleicher Durchmesser, Lage und Achse; dann gleiche Lage und Achse; dann gleicher Durchmesser und gleiche Achse. Die Lage einer Bohrung ist ihre Achslinie: ein anderer Punkt auf derselben Achse ist keine Änderung. Wer keinen Partner findet, ist neu oder entfallen. Verglichen werden Wert, gedruckter Text, Abmaße und Passung, Werte und Bezüge der Form- und Lagetoleranzen, die erwarteten Bohrungen, der Hüllquader und die Allgemeintoleranz.

`review.md` enthält bei vorhandener Änderungsliste den Abschnitt „Changes from the previous version“ (Feature, Before, After, Change), `review.json` die Liste unter `changes`. Die Web-Oberfläche zeigt sie in der Prüfliste (12).

## 9. Prüfliste und Rückverfolgbarkeit

`export_step` schreibt die Prüfliste als englischsprachige Markdown-Datei `review.md` und als `review.json`. `review.md` beginnt mit Titel, Teil, Zeichnung mit Seite, Maßstab und Allgemeintoleranz und endet mit der Zeile `All checks passed: yes` oder `All checks passed: no`. Dazwischen stehen diese Abschnitte:

| Abschnitt | Inhalt |
| --- | --- |
| Changes from the previous version | Nur bei einer Revision (8.3): Je Änderung Merkmal, Wert vorher und nachher, Art; steht vor den Annahmen |
| Assumptions and open items | Je Prüfeintrag: Maße, Problem, Annahme, Grund, Quelle auf der Zeichnung, Zahl der markierten Flächen in der STEP-Datei |
| Drawing notes | Hinweise der Zeichnung |
| Traceability | Je Eintrag in `refs`: Operation, Parameter, aktueller Wert, Maß-ID, gedruckter Text |
| Values without dimension reference | Größen einer Operation ohne `refs` und ohne gleich großes Maß (Abstand höchstens 0,01 mm): abgeleitete oder geschätzte Werte |
| Dimensions without model reference | Gedruckte Maße, die im Körper nicht vorkommen (`dims_found.missing`, Toleranz 0,05 mm) |
| Tolerances | Je toleriertem Maß: Nennmaß, Grenzmaße und Abmaße, Toleranzmitte, Quelle, Einhaltung beim Nennmaß (bei einem Urteil aus dem gemessenen Körper mit dem Zusatz „checked (geometry)“) und bei der Toleranzmitte |
| Export notes | Warnungen des Exports, zum Beispiel ein angenommenes Maß ohne PMI oder eine Passung ohne Tabelleneintrag |
| Geometric tolerances | Je Toleranzrahmen: Art, Wert, Bezüge, Text wie gedruckt und ob er als semantisches PMI in der STEP-Datei steht |
| PMI in the STEP file | Maße, die `read_pmi` in der geschriebenen STEP-Datei findet: Art, Wert, Toleranz oder Passung, Flächen, Beschreibung |
| Checks | Alle Prüfungen mit Soll, Ist und Ergebnis |

`review.json` enthält dieselben Daten unter `assumptions`, `notes`, `traceability`, `unreferenced`, `dims_found`, `tolerances`, `pmi`, `changes`, `warnings`, `validation`, `validation_basis` und `all_ok`. `validation_basis` nennt, was die Prüfungen belegen: die Konsistenz mit der erfassten Spezifikation, nicht den Abgleich mit der Zeichnung.

## 10. Toleranzen (`quality/tolerances.py`)

`quality/tolerances.py` ist an `export_step` angebunden: Der Export nutzt sie für die Einhaltung im Bericht und mit `variant="mid"` für den Aufbau auf den Toleranzmitten (8.1).

- **Grenzabmaße** eines Maßes, in dieser Reihenfolge: die gedruckten (`tol_upper` und `tol_lower`), die Passung nach ISO 286 (`fit`), die Allgemeintoleranz nach ISO 2768 (`general_tolerance`, nur für Längen, Durchmesser und Radien). Ohne Angabe sind beide Abmaße 0.
- **Tabellen:** Eingebaut sind ISO 2768-1 (f, m, c, v) und ISO 286-1 (H6 bis H11, h6 bis h11, g6, js6, k6, m6, n6, p6, r6, bis 500 mm) in `quality/iso_tables.py`. Eine Datei `*isolim*.xml` (ISO 286) oder `*toltabs*.xml` (ISO 2768) im Ordner `D2C_TABLES` ersetzt die jeweilige Tabelle ganz. Eine Passung ohne Eintrag oder ein Maß außerhalb der Tabelle ergibt eine Warnung, das Maß gilt als nicht geprüft; das Modul extrapoliert nicht.
- **Toleranzmitte:** Nennmaß plus Mittelwert der beiden Grenzabmaße, auf 6 Nachkommastellen gerundet. `apply_mode(ops, spec, "mid")` setzt jeden über `refs` gebundenen Wert auf die Toleranzmitte seines Maßes und gibt `(ops, shifts, skipped)` zurück: `shifts` nennt die verschobenen Werte mit Faktor, `skipped` die Bindungen, die unverändert bleiben (Wert gehört zu keinem Faktor oder die Tabelle kennt das Maß nicht), mit Grund. `unbound` nennt tolerierte Maße, deren Mitte vom Nennmaß abweicht und die keine brauchbare Bindung haben.
- **Halb- und Doppelmaß:** Ein gebundener Wert gehört zum Maß, zu dessen Hälfte (Radius statt Durchmesser, Versatz zur Mitte) oder zu dessen Doppeltem (Durchmesser statt Radius). Die Zuordnung prüft in dieser Reihenfolge: liegt der Betrag im Toleranzband des Maßes, dann seines Halbmaßes, dann seines Doppelmaßes. Das Vorzeichen zählt nicht, und die Toleranzmitte wird mit demselben Faktor und dem Vorzeichen des Werts gesetzt.
- **Nahe Werte bleiben ein Verstoß:** Liegt ein Wert außerhalb des Bands, aber höchstens max(0,5 mm; 5 % des Nennmaßes) vom Nennmaß, seiner Hälfte oder seinem Doppelten entfernt, gehört er zum Maß und wird als Verstoß gemeldet. Ein Wert, der zu keinem davon passt, ist kein Maßwert: Er bleibt beim Aufbau auf der Toleranzmitte unverändert und die Einhaltung führt ihn nicht als Verstoß, sondern nennt ihn in `note`.
- **Einhaltung:** `compliance` prüft für jedes tolerierte Maß die gebundenen Werte der Operationen und bei Durchmessern die gemessenen Bohrungen gegen die Grenzen (Spielraum 1e-6 mm). Eine gemessene Bohrung gehört zum Maß, wenn sie es in `dim_ids` nennt oder ihr erwarteter Durchmesser dem Nennmaß entspricht. Ein Maß ohne gebundene Werte wird zusätzlich gegen die Geometrie geprüft (`geometry`): Der Körper muss einen Zylinderdurchmesser, einen Ebenenabstand oder ein Lagemaß einer Zylinderachse (Achse zu Ebene, Achse zu Achse) dieser Größe enthalten (±0,05 mm um Nennmaß und Toleranzband). Welches Merkmal es ist, stellt die Prüfung nicht fest. Das Urteil für die Toleranzmitte in der Prüfliste stammt für Maße ohne gebundene Werte vom selben Körper, weil nur gebundene Parameter auf die Mitte wandern. Ohne Werte und ohne Fund hat `in_tolerance = null`: das Maß ist nicht geprüft.

## 11. Ergebnisablage

```
results/<teil>/
  spec.json, ops.json        nach submit_spec und nach jedem übernommenen Modellaufbau
  <teil>.step                nach erfolgreichem export_step: Modell (AP242 mit semantischem PMI)
  <teil>.glb, marks.json     nach erfolgreichem export_step: 3D-Ansicht und Zuordnung der Farben zu Prüfeinträgen
  pmi.json                   nach erfolgreichem export_step: Maße, die read_pmi in der STEP-Datei liest
  report.json                nach erfolgreichem export_step: Prüfbericht
  review.md, review.json     nach erfolgreichem export_step: Prüfliste
  <teil>_mid.step, report_mid.json   nach export_step mit variant="mid"
  runs/*.jsonl               Ereignisprotokoll jedes Laufs (Web-Oberfläche, `python -m d2c.agent` und `python -m d2c.agent.batch`)
  changes.json               Änderungsliste der laufenden Revision (8.3)
  versions/<Zeitstempel>/    früherer Stand des Teils, abgelegt bei einem Neubeginn oder einer Revision mit `submit_spec`
```

`results/EVAL.json` ordnet Teilen eine Zeile aus der Auswertung zu, die die Web-Oberfläche anzeigt.

Ein Laufprotokoll beginnt mit einer Zeile `meta` (run_id, Teil, Zeichnung, Modell, Startzeit); jede weitere Zeile ist ein Ereignis mit `at`, den Sekunden seit dem Start. Ein fehlendes `done` heißt, der Lauf wurde abgebrochen.

## 12. Web-Oberfläche

`python -m d2c.web [--host H] [--port P]` startet die Ansicht (Standard `127.0.0.1:8080`, also nur auf dem eigenen Rechner; im Container wird sie an `0.0.0.0` gebunden und der Port nur auf `127.0.0.1` des Hosts veröffentlicht). Das Compose-Profil startet den Dienst `app` mit `--host 0.0.0.0`, weil er im Containernetz erreichbar sein muss, und veröffentlicht den Port nur als `127.0.0.1:8080:8080`. `POST /api/runs` nimmt nur Anfragen der eigenen Seite an: Ein `Origin`, der nicht zum `Host` passt, oder `Sec-Fetch-Site: cross-site` ergibt 403; Anfragen ohne beide Header (curl, Skripte) bleiben erlaubt.

Gestaltung und Verhalten der Oberfläche: `docs/web-design.md`.

| Methode und Pfad | Zweck |
| --- | --- |
| `GET /` | Oberfläche (`static/index.html`) |
| `GET /api/models` | Modellwahl: Werte wie `claude/<m>`, `ollama/<m>` und `custom/<m>`; ohne gefundene Claude-Anmeldung sind die Claude-Optionen gesperrt |
| `GET /api/parts` | Alle Teile: Name, Titel, `all_ok`, Zahl der Prüfeinträge, Dateien, Zeile des Teils aus `results/EVAL.json`, falls vorhanden |
| `GET /api/parts/{name}` | Ein Teil wie oben, dazu Spec, Prüfliste, Bericht, die Liste seiner Laufprotokolle (`runs`, mit `running` für einen Lauf, den dieser Server noch ausführt) und der abgelegten Vorversionen (`versions`) |
| `GET /api/parts/{name}/drawing.png` | Erste Seite der Zeichnung des Teils, 100 dpi |
| `GET /api/parts/{name}/runs/{run_id}` | Ereignisse eines gespeicherten Laufs aus seinem Protokoll, ohne die Kopfzeile |
| `GET /files/{name}/{file}` | Datei aus dem Ergebnisordner des Teils |
| `POST /api/runs` | Zeichnung hochladen und Lauf starten; das Formularfeld `revise=true` stellt ein vorhandenes Teil auf die neue Zeichnung um (400, wenn das Teil keine `spec.json` und `ops.json` hat) |
| `GET /api/runs` | Aktive und zuletzt beendete Läufe dieses Servers; die Oberfläche hängt sich nach einem Neuladen wieder an |
| `GET /api/runs/{run_id}/events` | Ereignisse eines Laufs als Server-Sent Events |

- **Automatischer Start:** Die Oberfläche übernimmt beim Wählen oder Ablegen einer Datei den Teilnamen aus dem Dateinamen (Unicode-Buchstaben bleiben erhalten, höchstens 64 Zeichen) und sendet sofort `POST /api/runs` mit dem gewählten Modell. Die Option „Upload only“ unterdrückt den Start; ebenso ein Teilname, den es schon gibt, weil das alte Ergebnis dann als frühere Version zur Seite wandert (anders bei gewählter Revisionsoption). Die Schaltfläche „Create STEP“, nach dem ersten Start „Restart“, startet den Lauf von Hand. Ist kein Modell wählbar, zeigt die Oberfläche die Meldung des Servers und startet nichts.
- **Revision:** Die Option „Update existing model (new revision)“ ist nur wählbar, wenn der Teilname schon existiert; sie sendet `revise=true` und unterdrückt die Rückfrage zum vorhandenen Teil. Der Server antwortet mit 400, wenn das Teil keine `spec.json` und `ops.json` hat. Die Prüfliste der Oberfläche zeigt die Änderungsliste aus `review.json`.
- **Hochladen:** Formularfelder `pdf`, `part` und optional `model` (sonst `D2C_LLM_MODEL`) und `revise`. Angenommen werden PDF (Signatur `%PDF`) sowie PNG und JPG (an der Dateisignatur, nicht an der Endung); ein Bild wird zu einer einseitigen PDF-Datei umgewandelt. Die Datei wird unter `<D2C_DATA>/uploads/<part>.pdf` gespeichert, die Antwort ist `{"run_id": ...}`. Der Teilname ist höchstens 64 Zeichen lang und darf Umlaute enthalten. Antworten: 413 bei mehr als 20 MB oder mehr als 50 Megapixeln eines Bildes; 400 bei fehlender Datei, ungültigem Teilnamen, anderem Dateityp, beschädigter oder passwortgeschützter PDF, einem Modell, das `GET /api/models` nicht nennt, fehlendem Modell bei gesetztem `D2C_LLM_BASE_URL` oder fehlender Claude-Anmeldung ohne Endpunkt (Meldung: kein Sprachmodell verfügbar, mit Hinweis auf Ollama, Claude Code und `.env`); 403 bei fremdem `Origin`; 409, wenn zu diesem Teil schon ein Lauf aktiv ist; 429, wenn `D2C_MAX_RUNS` Läufe gleichzeitig laufen.
- **Modellwahl (`GET /api/models`):** Claude-Modelle (Sonnet, Opus, Haiku) als `claude/<m>`, die Modelle der Ollama an `D2C_OLLAMA_URL` als `ollama/<m>` und das Modell aus `D2C_LLM_BASE_URL` und `D2C_LLM_MODEL` als `custom/<m>`. Eine Claude-Anmeldung ist gefunden, wenn `ANTHROPIC_API_KEY` oder `CLAUDE_CODE_OAUTH_TOKEN` gesetzt ist oder die Anmeldung von Claude Code auf diesem Rechner existiert; sonst sind die Claude-Optionen gesperrt.
- **Agent:** Ein Lauf ruft `run_agent` aus `d2c.agent.runner` als Coroutine auf; `on_event` erhält jedes Ereignis als Objekt. Das Paket `d2c.agent` (4.6) erreicht das Modell über den Claude-Login oder einen Anthropic-Schlüssel (`claude/<m>`), über die Ollama an `D2C_OLLAMA_URL` (`ollama/<m>`) oder über den Anthropic-kompatiblen Endpunkt `D2C_LLM_BASE_URL`, etwa OpenRouter (`custom/<m>` oder freier Modellname). Ohne Modellwahl wird der Wert von `D2C_LLM_API_KEY` als Anthropic-Schlüssel verwendet, sonst die Anmeldung von Claude Code.
- **Ereignisse:** Je Ereignis eine Zeile `data: <JSON>`, Typen sind `phase`, `tool_call`, `tool_result`, `message`, `stderr` und `done`. Das letzte Ereignis hat den Typ `done`: vom Agenten selbst, sonst aus seinem Rückgabewert, bei einer Ausnahme mit `ok = false` und `reason`. `done` kann `limit = budget` oder `limit = time` tragen, wenn das Kosten- oder Zeitlimit den Lauf beendet hat. Danach endet der Strom. Läufe und Ereignisse liegen im Speicher des Prozesses (`GET /api/runs` nennt die aktiven und die letzten beendeten); jede Verbindung erhält alle Ereignisse ab dem ersten. Zusätzlich schreibt jeder Lauf sein Protokoll nach `results/<teil>/runs/` (11); der Tab „Runs“ der Oberfläche spielt eines davon mit den aufgezeichneten Zeitabständen erneut ab.
- **Dateinamen** in Pfaden dürfen weder `/`, `\`, `..` noch NUL enthalten.
- **Statische Dateien** unter `/static/`: `index.html`, `style.css` und `app.js`, dazu `vendor/model-viewer.min.js` (Lizenz in `vendor/model-viewer.LICENSE`) und die Schriften `InterTight` und `JetBrains Mono` unter `vendor/fonts` mit deren Lizenzen.

## 13. Konfiguration

### 13.1 Umgebungsvariablen

| Variable | Standard | Zweck |
| --- | --- | --- |
| `D2C_DATA` | `/data` | Ordner der Zeichnungen |
| `D2C_RESULTS` | `/app/results` | Ergebnisordner |
| `D2C_TABLES` | `/tables` | eigene Toleranztabellen (`quality/tolerances.py`); ohne Dateien gelten die eingebauten |
| `D2C_TIMEOUT` | `60` | Zeitlimit eines Modellaufbaus in Sekunden |
| `D2C_MAX_RUNS` | `2` | gleichzeitige Läufe der Web-Oberfläche, weitere ergeben 429 |
| `D2C_MAX_USD`, `D2C_MAX_MINUTES` | `3`, `20` | Kosten- (USD) und Zeitlimit (Minuten) je Agentenlauf, `0` = aus |
| `D2C_LOG` | `info` | Grad der Protokolle von Server und Web-App auf stderr: `debug`, `info`, `warning` oder `error` |
| `D2C_LLM_BASE_URL`, `D2C_LLM_API_KEY`, `D2C_LLM_MODEL`, `D2C_LLM_BUILD_MODEL` | leer | Endpunkt und Modelle für Läufe aus der Web-Oberfläche und der Kommandozeile |
| `D2C_LLM_BUILD_BASE_URL` | leer | Endpunkt der Bauphase, falls er vom Endpunkt der Lesephase abweicht („Cloud liest, lokal baut“) |
| `D2C_LLM_CONTEXT` | `64000` | Kontextfenster in Token für Modelle ohne Claude-Namen an einem eigenen Endpunkt |
| `D2C_LLM_THINKING` | leer | Obergrenze der Denk-Token für solche Modelle |
| `D2C_REPORT` | leer | `full`: die Bauwerkzeuge antworten mit dem vollen Bericht statt des kompakten (5.4) |
| `D2C_REF_DATA` | `/app/data` | Ordner der Referenzmodelle für `scripts/eval_refs.py`; der Server liest ihn nie |
| `D2C_OLLAMA_URL` | `http://localhost:11434` | Ollama, dessen Modelle die Modellwahl listet und das `ollama/<m>`-Läufe erreicht |
| `D2C_MCP_URL` | `stdio` | Verbindung des Laufs zum MCP-Server |
| `ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN` | leer | Zugang des Agenten zum Claude-Backend; ohne beide zählt die Anmeldung von Claude Code auf diesem Rechner |

`config.py` liest alle D2C-Variablen bei jedem Aufruf neu; `D2C_LOG` gilt ab dem Start des Prozesses.

### 13.2 Datenordner

Die Ordner liegen außerhalb des Container-Images und werden beim Start eingehängt.

| Ordner im Repo | Im Container | Inhalt |
| --- | --- | --- |
| `drawings/` | `/data` | PDF-Zeichnungen, Unterordner `uploads/` für hochgeladene Zeichnungen |
| `results/` | `/app/results` | Ergebnisse |
| `tables/` | `/tables` | optional: eigene Toleranztabellen, ersetzen die eingebauten (im Container nur lesend eingehängt) |

## 14. Sicherheit und Isolation

- **Netzwerk:** Der Agent erreicht den Server über `D2C_MCP_URL`: `stdio` (Standard), eine http(s)-URL oder eine Befehlszeile wie ein `docker run --network none` je Lauf. Mit `docker run --network none` hat der Server keinen Netzwerkzugang. Der HTTP-Modus ist optional. Gebunden an `127.0.0.1`, `localhost` oder `::1` nimmt er nur Loopback-Werte in den Headern `Host` und `Origin` an und antwortet sonst mit 421, im Container wird er deshalb an `0.0.0.0` gebunden; im Compose-Betrieb liegt er im internen Netz `sandbox`, das nur die Web-App erreicht.
- **Modellaufbau:** Jeder Durchlauf läuft in einem eigenen Prozess `python -I -m d2c.cad.worker` in einer eigenen Prozessgruppe, mit temporärem Arbeitsordner und nur der Umgebungsvariablen `PATH`. Grenzen: Zeitlimit `D2C_TIMEOUT`, danach wird die Prozessgruppe beendet; Adressraum 4 GiB; Dateigröße 200 MiB; CPU-Zeit 4 · ⌈Zeitlimit⌉ + 10 s. Eine Grenze, die das Betriebssystem nicht setzen kann, wird nur auf macOS übersprungen (es lehnt die Adressraumgrenze ab); unter Linux, dem Betrieb im Container, wird der Fehler weitergereicht, damit der Worker nie unbegrenzt läuft.
- **Skripte (Positivliste):** Ein `run_script`-Skript sieht nur `cq.<Klasse>` für `Workplane`, `Vector`, `Plane`, `Sketch`, `Solid`, `Compound`, `Location`, `Edge`, `Wire`, `Face` und `Shape`, das Modul `math`, `result` und einfache Builtins (`range`, `len`, `sorted`, `min`, `max`, `abs`, `round`, `enumerate`, `zip`, `list`, `dict`, `tuple`, `float`, `int`, `str`, `bool`, `set`, `any`, `all`, `isinstance`, `reversed`, `divmod`, `pow`, `map`); das Modul `cq` selbst ist nicht erreichbar. Zusätzlich lehnt die Prüfung des Syntaxbaums Importe, Namen und Attribute für Dateien, Prozesse, Module und Code-Ausführung (`open`, `exec`, `eval`, `os`, `sys`, `subprocess`, `system`, `popen` und weitere), die Rahmen laufender Generatoren und Ausnahmen (`gi_frame`, `f_globals`, `tb_frame` und weitere), Methoden mit den Präfixen `export` und `import` sowie jeden Namen mit führendem Unterstrich ab. Die Prüfung liefert verständliche Fehlermeldungen; die Grenze bilden der schmale Namensraum, der Prozess und der Container.
- **Pfade:** Zeichnungen nur als `.pdf` unterhalb von `D2C_DATA`, aufgelöst über ihr tatsächliches Ziel. Teilnamen nur aus Buchstaben, Ziffern, `_` und `-`.
- **Container:** Das Image läuft als Nutzer `app` (uid 1000), nicht als root. `start.sh` und Compose starten es mit schreibgeschütztem Dateisystem (`--read-only`) und `tmpfs` für `/tmp`; beschreibbar sind nur die eingehängten Ordner `results/` und (für Uploads) `drawings/`.
- **Web-Oberfläche:** Sie hat keine Anmeldung und lauscht standardmäßig nur auf `127.0.0.1`; Compose veröffentlicht den Port nur auf `127.0.0.1`. Für Zugriff aus dem Netz gehört ein Reverse-Proxy mit Anmeldung davor. Schranken: Prüfung des `Origin` bei `POST /api/runs` (403; sonst könnte jede Webseite im Browser des Nutzers Läufe mit dessen Claude-Zugang starten), `D2C_MAX_RUNS` gleichzeitige Läufe (429), ein aktiver Lauf je Teil (409), Modell nur aus `GET /api/models`, Pixelgrenze 50 MP und Teilnamen bis 64 Zeichen. Dateien unter `/files` werden über ihr tatsächliches Ziel aufgelöst und müssen im Ergebnisordner des Teils liegen.
- **Geheimnisse:** Zugangsdaten nur über Umgebungsvariablen, nie im Repository. An einen stdio-Server reicht der Agent die D2C-Einstellungen ohne LLM- und Schlüsselvariablen weiter, Schlüssel und Token bleiben leer.

## 15. Betrieb

```
# Lokal: Web-App auf 127.0.0.1, MCP-Server je Lauf ohne Netzwerk im Docker-Container
./start.sh              # baut das Image und startet die Web-Oberfläche, Standard-Port 8080 (PORT)

# Alles in Containern: der MCP-Dienst liegt im internen Netz sandbox, die Web-App bedient Port 8080 (veröffentlicht auf 127.0.0.1)
docker compose up --build

# Container-Image bauen und Tests ausführen
docker build -t d2c .
docker run --rm d2c pytest -q

# Demo ohne Sprachmodell: alle Prüfungen am Demo-Teil
docker run --rm --network none d2c python -m d2c.demo

# Beispielergebnis (examples/demo) gegen einen frischen Demo-Lauf prüfen; --update schreibt es neu
docker run --rm --network none d2c python scripts/check_example.py

# Stapellauf: jede PDF eines Ordners über den Agenten (Modell und Zugang wie bei python -m d2c.agent)
python -m d2c.agent.batch <zeichnungen> --jobs 3 --out results

# Gespeicherte Teile erneut aufbauen (nichts schreiben) und mit --export die STEP-, Report- und Review-Dateien neu schreiben
docker run --rm -v "$PWD/results":/app/results d2c python scripts/replay.py [--export] [teil ...]

# Exportierte Modelle gegen die Referenz-STP der Zeichnung vergleichen (schreibt EVAL.md und EVAL.json)
docker run --rm -v "$PWD/results":/app/results -v "$PWD/drawings":/data:ro -v <Referenzordner>:/app/data:ro d2c python scripts/eval_refs.py

# Exportierte STEP-Datei unabhängig in FreeCAD prüfen (freecadcmd auf dem Rechner, nicht im Image)
python scripts/freecad_check.py results/<teil>/<teil>.step --spec results/<teil>/spec.json [--tol MM] [--mid]

# Messreihe über Modelle, Teile und Modi (full, build, hybrid)
python scripts/bench_models.py --models <modell> ... --parts demo <teil> ... --mode full --out <ordner>

# MCP-Server über stdio, ohne Netzwerk
docker run -i --rm --network none --read-only --tmpfs /tmp -v "$PWD/drawings":/data:ro -v "$PWD/results":/app/results d2c

# MCP-Server über HTTP, Endpunkt http://localhost:8765/mcp
docker run --rm --read-only --tmpfs /tmp -p 127.0.0.1:8765:8765 d2c python -m d2c.server --http 0.0.0.0:8765

# Web-Oberfläche auf Port 8080, nur lokal erreichbar
docker run --rm --read-only --tmpfs /tmp -p 127.0.0.1:8080:8080 -v "$PWD/results":/app/results -v "$PWD/drawings":/data d2c python -m d2c.web --host 0.0.0.0
```

`start.sh` übergibt eine vorhandene `.env` neben dem Skript mit `uv run --env-file` an die Web-Oberfläche (die Werte werden nicht als Shell-Befehle ausgeführt), setzt `D2C_DATA`, `D2C_RESULTS` und `D2C_TABLES` auf die Repo-Ordner und `D2C_MCP_URL` auf eine Befehlszeile mit `docker run -i --rm --network none`, die die Ordner als Volumes einhängt; die Web-Oberfläche läuft mit `uv` auf dem eigenen Rechner und nutzt die Anmeldung von Claude Code. Die Demo liest die Zeichnung, die Spezifikation und die Toleranztabellen aus `d2c/demo/` (`D2C_TABLES` ist in diesem Lauf vorbelegt) und schreibt nach `/tmp/d2c-demo` im Container; mit `-v "$PWD/out":/out` und `--out /out` bleiben die Dateien erhalten. Sie gibt je Prüfung eine Zeile `PASSED` oder `FAILED` aus, dann die Annahmen und die geschriebenen Dateien und zuletzt die Zeile `<n> of 3 parts exported, all checks passed` (sonst `not all checks passed`). Der Rückkehrcode ist 0 nur bei erfolgreichem Export.

Tests mit dem Marker `refdata` brauchen Referenzdaten unter `/app/data` und werden sonst übersprungen. Messergebnisse: `docs/ergebnisse.md`.

## 16. Grenzen

- Maße werden nur aus Vektorgeometrie gemessen, also bei Vektor-PDFs. Bei gescannten Zeichnungen und Bildern liest das Modell die Maße aus dem gerenderten Bild. Gemessen wurde das System nur an Vektor-PDFs.
- Die Prüfungen (7) vergleichen das Modell mit der erfassten Spezifikation (`expected`), nicht mit der Zeichnung. Sie belegen Konsistenz, nicht die richtige Lesung der Zeichnung; ein falsch gelesenes Maß in `expected` besteht die Prüfung. `dims_found` und die Prüfliste nennen gedruckte Maße, die im Körper fehlen.
- Entformungsschrägen, Senkungen, kleine Bohrungen und Innenräume, die nur aus Schnittansichten hervorgehen, werden vom Modell nicht zuverlässig erfasst.
- Lokale Sprachmodelle sind angebunden, ihre Qualität ist nicht gemessen.
- Form- und Lagetoleranzen stehen als semantisches PMI in der STEP-Datei, aber ohne grafische Darstellung. Gewinde werden als Kernlochbohrung modelliert.
- Maße mit zwei gleichsinnigen Abmaßen (etwa −0,005/−0,014) stehen richtig in der STEP-Datei, der OpenCascade-Leser des Rückimports gibt sie aber falsch wieder (Vorzeichen der unteren Abweichung, bei zwei negativen Abmaßen als Bereich). Die aus dem Rückimport erzeugten `pmi.json` und PMI-Zeilen der Prüfliste zeigen solche Maße deshalb nicht verlässlich; Passungen (`fit`) und Abmaße mit verschiedenen Vorzeichen sind nicht betroffen.
- Nur Einzelteile aus einem Volumenkörper, keine Baugruppen, keine Freiformflächen.
- Komplexe Teile können vereinfacht werden; die Prüfliste nennt jede Annahme und Vereinfachung.
- Ob ein STEP in einem kommerziellen CAD-System fehlerfrei öffnet, ist nicht geprüft. STEP-Dateien werden mit CadQuery (Rückimport) und mit FreeCAD (7.1) geprüft; beide beruhen auf OpenCascade.
- Das GLB enthält Farben, aber keine Namen, kein PMI und keine Ebenen; die Zuordnung der Farbe zu den Prüfeinträgen steht in `marks.json`.
- Eingebaut sind nur gängige Passungen (H6 bis H11, h6 bis h11, g6, js6, k6, m6, n6, p6, r6, bis 500 mm); andere, etwa E8 oder H12, brauchen eigene Tabellen und ergeben sonst eine Warnung. Beim Aufbau auf Toleranzmitte werden nur Bindungen verschoben, deren Wert zu einem Faktor des Maßes gehört (1, 0,5, 2 und die negativen Werte).
- Die Web-Oberfläche hat keine Anmeldung und ist nur lokal erreichbar; für Zugriff aus dem Netz gehört ein Reverse-Proxy mit Anmeldung davor.
- Der Server verwaltet alle Teile in einer Sitzung ohne Sperren. Gleichzeitige Aufrufe für dasselbe Teil sind nicht vorgesehen.
