# WP-08: Web-Oberfläche: Frontend, Compose, Bild-Upload

| Teil | Profil | Aufwand | Braucht vorher | Gate |
|---|---|---|---|---|
| 1 Ergebnis-Browser (Frontend) | C (Frontend und DevOps) | 2 h | WP-00 (3D-Modell erst nach WP-02) | G2, Sa 15:00 |
| 2 Upload, Live-Protokoll, Bild-Upload, Compose | C | 2 h | Compose nach WP-03 Teil 2, echte Live-Läufe nach WP-06 | G3, Sa 20:00 |

## Ziel

Eine Seite im Browser zeigt alle erzeugten Teile: Zeichnung und 3D-Modell nebeneinander, die orange markierten Annahmen, Review-Liste, Validierung, Benchmark und Downloads. Dazu ein Upload, der einen Live-Lauf startet. Das ist das Demo-Bild für Pitch und README (P1, P2) und Meilenstein 1 der Folien („2D drawing upload and analyze automatically“).

## Kontext

- **Backend existiert und ist grün** (`d2c/web/app.py`, 196 Zeilen, `tests/test_web.py` mit 10 Tests). `create_app(agent=None)` ist eine Starlette-App, die Pfade kommen aus `D2C_RESULTS` und `D2C_DATA`. Start: `python -m d2c.web [--host 0.0.0.0] [--port 8080]`.
- Vorhandene Endpunkte:
  - `GET /` liefert `static/index.html`, `GET /static/...` die statischen Dateien.
  - `GET /api/parts` → Liste mit `name`, `title`, `all_ok`, `review_count`, `eval` (Zeile aus `EVAL.json`, verknüpft über den Schlüssel `part`), `files`.
  - `GET /api/parts/{name}` → dasselbe plus `spec`, `review`, `report` (Inhalt der JSON-Dateien).
  - `GET /api/parts/{name}/drawing.png` → erste Seite der Zeichnung, 100 dpi.
  - `GET /files/{name}/{file}` → Datei aus `results/<name>/` (Pfadprüfung vorhanden).
  - `POST /api/runs` (Formularfelder `pdf`, `part`, `model`) → speichert unter `<D2C_DATA>/uploads/<teil>.pdf`, startet den Agenten, Antwort `{"run_id"}`. Ohne `D2C_LLM_BASE_URL` → 503.
  - `GET /api/runs/{run_id}/events` → Server-Sent Events, endet mit `done`.
- `review.json` hat die Schlüssel `assumptions`, `notes`, `traceability`, `unreferenced`, `validation`, `all_ok` (`d2c/review.py`, `to_json`).
- Vorhandene Tests: `test_parts_list_and_detail`, `test_eval_row_joins_by_part`, `test_file_download_and_traversal_rejected`, `test_drawing_png`, `test_run_streams_events`, `test_run_appends_done_and_reports_agent_crash`, `test_upload_saved_under_uploads`, `test_upload_rejects_bad_input`, `test_run_without_llm_endpoint_is_503`, `test_index_and_vendored_viewer_served`.
- **Frontend fehlt:** `static/index.html` ist nur ein Gerüst (Überschrift, Upload-Formular, leere Liste). Es bindet `/static/app.js` schon ein, die Datei gibt es aber nicht. `static/style.css` und `static/vendor/model-viewer.min.js` (Apache-2.0, mit Lizenzdatei) sind da.
- Mit `pytest -W error` scheitern heute 7 Web-Testfälle an einer `ResourceWarning`: `start_run` schließt das hochgeladene Formular nicht.
- Die GLB mit Farben schreibt erst der Export aus WP-02 (`<teil>.glb`). Bis dahin zeigt die 3D-Ansicht einen Hinweis statt des Modells.
- Starlette kommt mit `mcp`. Keine neue Python-Abhängigkeit, kein Build-Schritt fürs Frontend, kein CDN.

## Dateien

`d2c/web/static/index.html`, `d2c/web/static/app.js` (neu), `d2c/web/static/style.css`, `d2c/web/app.py` (nur Teil 2), `tests/test_web.py`, `docker-compose.yml`, `pyproject.toml` (nur `package-data`), `docs/img/web-ui.png`.

## Aufgaben

**Teil 1 (bis G2): Ergebnis-Browser**

1. Backend gegen die Liste oben lesen. Abweichungen zu `spec.md` melden, nicht still ändern.
2. `app.js` (reines JavaScript, `fetch`, keine Bibliothek außer `model-viewer`):
   - links die Teileliste aus `/api/parts`: Name, Statuspunkt (`all_ok`), Anzahl Review-Einträge,
   - rechts der Kopf mit Titel, Zeichnung (`spec.drawing`), Maßstab, `all_ok`,
   - darunter Zeichnung (`drawing.png`) und 3D-Ansicht (`<model-viewer>` mit `/files/<teil>/<teil>.glb`, falls in `files`) nebeneinander,
   - darunter Reiter: **Review** (`assumptions`: id, Maße, Problem, Annahme, Grund, markierte Flächen; Annahmen im Akzent), **Validation** (`validation`), **Traceability** (`traceability` und `unreferenced`), **Benchmark** (`eval`, falls vorhanden), **Downloads** (alle Dateien aus `files`).
   - Leere, Lade- und Fehlerzustände als klarer Text, z. B. „No parts yet. Run the demo or upload a drawing.“
3. `pyproject.toml`: `packages = ["d2c"]` durch `[tool.setuptools.packages.find]` mit `include = ["d2c*"]` ersetzen. Heute fehlt `d2c.web`, eine Installation ohne `-e` hätte keine Web-App. Eine feste Liste `["d2c", "d2c.web"]` würde den Docker-Build aus WP-00 brechen, der die Abhängigkeiten gegen ein leeres Paket installiert. Unter `[tool.setuptools.package-data]` zusätzlich `"d2c.web" = ["static/*", "static/vendor/*"]`.
4. In `start_run` das Formular mit `async with request.form() as form:` lesen. Danach ist die Suite auch mit `-W error` grün.

**Teil 2 (bis G3): Upload, Live-Protokoll, Bild-Upload, Compose**

5. Oben im Frontend: Upload-Formular (Datei, Teilname, optional Modell) und Live-Protokoll über `EventSource` auf `/api/runs/{id}/events`: eine Zeile je Ereignis, am Ende `done` mit Ergebnis, danach die Teileliste neu laden.
6. **Bild-Upload** (R1, Scans und Bilder): `POST /api/runs` nimmt außer PDF auch PNG und JPG an (Prüfung der Dateisignatur, nicht der Endung) und wandelt Bilder mit PyMuPDF in ein einseitiges PDF um (`pymupdf.open(stream=..., filetype=...)`, dann `convert_to_pdf()`). Gespeichert wird immer `uploads/<teil>.pdf`.
7. **Größenlimit:** Uploads über 20 MB → 413 mit klarer Meldung.
8. **`docker-compose.yml`**:
   - Netz `sandbox` mit `internal: true`.
   - Beide Dienste mit `build: .` und `image: d2c:latest`. So zieht Compose nie ein fremdes Image gleichen Namens aus dem Netz.
   - Dienst `mcp`: `command: python -m d2c.server --http 0.0.0.0:8765`, nur Netz `sandbox`, Volumes `./drawings:/data:ro`, `./tables:/tables:ro`, `./results:/app/results`.
   - Dienst `app`: `command: python -m d2c.web --port 8080`, Netze `sandbox` und `default`, Port `8080:8080`, Umgebung `D2C_MCP_URL=http://mcp:8765/mcp`, `env_file` als `- path: .env` mit `required: false` (braucht Docker Compose 2.24 oder neuer), Volumes `./results:/app/results` und `./drawings:/data` (schreibbar, nur für `uploads/`), `extra_hosts: ["host.docker.internal:host-gateway"]` für Ollama auf dem Host.
   - Ohne `.env` startet alles, Uploads antworten dann mit 503 und dem Hinweis auf `.env.example`.

## Tests (zuerst schreiben)

Starlette `TestClient`, tmp-Ergebnisordner mit dem exportierten Demo-Teil. Die zehn vorhandenen Tests bleiben grün. Neu:

- `test_app_js_served`: `GET /static/app.js` liefert 200 mit JavaScript.
- `test_upload_png_converted_to_pdf`: PNG-Upload (mit eingesetztem Fake-Agenten) → `uploads/<teil>.pdf` beginnt mit `%PDF` und hat eine Seite.
- `test_upload_too_large_is_413`.

Das Frontend selbst prüft niemand automatisch. Dafür gibt es die Sichtprüfung in der Abnahme.

## Gestaltung (dieselbe Sprache wie das Pitch-Deck)

- Farben: Hintergrund `#f4f5f6`, Text `#131619`, gedämpft `#59616a`, Linien `#c9ced3`, Flächen `#ffffff`, **ein** Akzent `#ef7d1a` (Annahme, Review), Grün `#3fae58` nur für „ok“ und Gewinde.
- Eckenradius 0 überall. Schrift „Helvetica Neue“, Helvetica, Arial; Monospace „SF Mono“, Menlo. Keine Verläufe, keine Schatten außer 1-px-Linien, keine Emoji.
- Sprache der Oberfläche: Englisch.

## Abnahme

- Tests grün, komplette Suite grün.
- Teil 1: Mit dem Demo-Teil in `results/` (`docker run --rm -v "$PWD/results":/app/results d2c python -m d2c.demo --out /app/results`) zeigt `python -m d2c.web` die Liste, die Zeichnung, alle Reiter und die Downloads. Nach WP-02 erscheint das 3D-Modell mit oranger Fläche.
- Teil 2: `docker compose config` gültig. `docker compose up` startet beide Dienste, `http://localhost:8080` zeigt die Teile. Ein Upload ohne `.env` zeigt die 503-Meldung. Danach `docker compose down`.
- Screenshot `docs/img/web-ui.png` (1440 × 900) mit dem Demo-Teil. Ein Bild mit einem BCT-Teil nur, wenn BCT die Veröffentlichung freigegeben hat (WP-16). Er geht ins README und ins Deck.

## Fallen

- Kein CDN zur Laufzeit. `model-viewer` liegt lokal unter `static/vendor/`.
- Der MCP-Dienst muss an `0.0.0.0` binden, sonst weist mcp 2.3 Anfragen mit `Host: mcp:8765` ab (WP-03, Fallen).
- Docker legt fehlende Ordner für Volumes selbst an (unter Linux mit Besitzer root). Der KI-Prüfer hat keine BCT-Daten, `docker compose up` muss trotzdem starten.
- SSE-Verbindungen hinter Proxys puffern. Für den lokalen Betrieb reicht `text/event-stream` mit `Cache-Control: no-cache` (ist schon so).
