# WP-00: Repos zusammenführen, Image bauen, Arbeitsweise

| Profil | Aufwand | Braucht vorher | Gate |
|---|---|---|---|
| A (Core, Abu: Kern einspielen) zusammen mit Kristian (Repo-Inhaber: Historie, Rechte) | 45 min | Kickoff-Entscheidungen 2 und 3 (README Abschnitt 8) | G0, Sa 11:15 |

## Ziel

Ein Repo, ein grüner Stand auf `dev`, alle vier können klonen, bauen und testen. Danach starten die anderen Pakete.

## Kontext

Es gibt zwei Codestände:

| | Team-Repo `KristianKalfic/DRAWING2CADMSN` | Kern aus `Archiv.zip` (Abu) |
|---|---|---|
| Umfang | drei Python-Dateien (etwa 650 Zeilen), 34 Tests | Paket `d2c/` (etwa 4.100 Zeilen), 254 Tests: 239 grün, 15 brauchen BCT-Daten |
| MCP | `mcp<2`, `FastMCP`, 13 Tools in einer Datei | `mcp==2.3.0`, `MCPServer`, 14 Tools, getestet, Review offen (WP-01) |
| Geometrie | Box, Zylinder, Polygon-Profil; Bohrungen und Taschen nur oben oder unten; Kanten per Selektor | Extrusion, Rotation, Bohrungen auf jeder Ebene (auch Kreismuster und Gewinde), Pfad- und Hüllprofile mit Bögen, Schnittmenge, Entformung, Kantenwahl per Punkt |
| Prüfung | Solid, BBox, Lage, Bohrungen (konkav), Re-Import | dasselbe, dazu Toleranzen, Markierungen, PMI-Rücklesen |
| Zeichnung lesen | nein (PyMuPDF installiert, ungenutzt) | rendern, Textebene, Vektormessung, Maßstabsprüfung |
| Export | STEP (AP214) | STEP AP242 mit semantischem PMI und Farben, GLB |
| Eigene Stärken | **Vergleich zweier Extraktionen, Plausibilitätsregeln, FreeCAD-Gegenprüfung, Windows-Anleitung** | Review-Pflicht im Schema, Rückverfolgung, Sandbox mit rlimits |
| Image | micromamba, Python 3.11 | `python:3.12-slim` mit pip, amd64 und arm64 geprüft |

**Empfehlung:** Der Kern wird die Basis, weil er fast alle Muss- und Kann-Anforderungen schon abdeckt und getestet ist. Der Prototyp zieht nach `prototype/` um und bleibt bis zum Freeze lesbar, danach hält ihn der Tag `prototype-v0` fest (WP-18). Seine Stärken übernimmt WP-19 als eigene Module auf Basis der `PartSpec`.

**Achtung, Historie:** 11 der 13 Commits im Team-Repo stammen von einem automatischen Autor-Konto, nicht von einem Teammitglied. Ihre Nachrichten tragen außerdem Co-Author-Zeilen und Sitzungslinks, eine Commit-Überschrift und vier Zeilen im Prototyp-README nennen den Chat-Client. Prüfen: `git log --format='%an <%ae>' | sort | uniq -c` und `git log --format=%B | grep -c -i -E 'co-authored|code/session_'`. Das Repo ist öffentlich, Jury und KI-Prüfer sehen es.

## Dateien

Alles im Team-Repo: `prototype/` (verschoben), `d2c/`, `tests/`, `scripts/`, `Dockerfile`, `pyproject.toml`, `README.md` (vorläufig), `.gitignore`, `.dockerignore`, `.gitattributes`. **Nicht** ins Repo: `docs/team/` (dieser Plan), `docs/superpowers/` aus dem Archiv, BCT-Daten.

## Aufgaben

**Vorbereitung (Abu, vor dem Kickoff):** Schritte 4 bis 10 in einem lokalen Ordner vorbereiten, einmal bauen und testen. Nach dem Kickoff bleiben dann nur Kopieren, Commit und Push. So hält G0.

1. **Sicherung** (Kristian): `git clone --mirror https://github.com/KristianKalfic/DRAWING2CADMSN.git d2c-backup.git`. Die Sicherung bleibt lokal bei Kristian.
2. **Historie** (Kristian, Kickoff-Entscheidung 2). Empfohlen ist Variante a.
   - **a) Neu beginnen** (5 Minuten, keine Zusatzwerkzeuge): Im frischen Klon `git checkout --orphan fresh`, Schritt 3 ausführen, `git add -A` (einmalig erlaubt, der Klon enthält nur den Prototyp) und als eigener Commit `chore: first prototype (JSON to CadQuery, mcp<2)`. Dann `git branch -M fresh main` und `git push --force-with-lease origin main`. Die Dateien bleiben, ihre Zwischenstände nicht.
   - **b) Historie umschreiben:** Autor und Committer der 11 Commits auf die echte Person setzen, die beiden Trailer-Zeilen (`Co-Authored-By:` und die Zeile mit `/code/session_`) aus jeder Nachricht entfernen, die eine Überschrift neutral formulieren, danach Force-Push. Nur wenn jemand sicher mit `git filter-repo` umgehen kann.
   - **Ohne Bereinigung:** `git switch -c dev`, dann Schritt 3 als normaler Commit auf `dev`.
   - Danach klonen alle neu. Alte Commits bleiben bei GitHub noch eine Weile über ihre SHA abrufbar, für die Jury zählt der abgegebene Stand.
3. **Prototyp verschieben und neutralisieren:**
   - `mkdir prototype`, dann `git mv server.py json_builder.py issue_check.py examples tests Dockerfile environment.yml README.md prototype/`.
   - Die Client-Konfigurationsvorlage umbenennen: `git mv *_config.example.json prototype/mcp_client_config.example.json`.
   - In `prototype/server.py` Zeile 6 und in `prototype/README.md` Zeile 9 den Namen des Chat-Clients durch „ein MCP-Client“ bzw. „das Sprachmodell“ ersetzen. In `prototype/issue_check.py` Zeile 5 den Modellnamen streichen („zweier Extraktionen“ genügt). Den Abschnitt zur Client-Einrichtung unter Windows im README (Zeilen 52 bis 70) durch einen Satz ersetzen: „Setup for MCP clients: see the main README.“ Danach muss `git grep -n -i -E 'desktop|opus' -- prototype` leer sein.
   - Die Tests des Prototyps finden ihre Module weiter, `prototype/tests/conftest.py` setzt den Pfad relativ zur eigenen Datei.
4. **Kern einspielen** (Abu) auf `dev` (nach Variante a oder b: `git switch -c dev` von `main`): Aus `Archiv.zip` `d2c/`, `tests/`, `scripts/` und `pyproject.toml` kopieren. **Nicht** kopieren: `__MACOSX/`, alle `__pycache__/` und `*.pyc`, `data/`, `drawings/`, `tables/`, `results/`, `docs/`, dazu `Dockerfile` und `README.md` (beide entstehen neu in Schritt 6 und 10). Server und Web kommen mit, sie gehören zu WP-01 und WP-08.
5. **BCT-Tests auslagern:** Die Tests mit `@pytest.mark.bct` prüfen gegen Maße aus BCT-Zeichnungen (Bohrungslagen am Fuss, Maße am 3-Loch-Hebel und an der Schnittmenge). Ohne Freigabe von BCT (WP-16, Frage 3) gehören diese Werte nicht ins öffentliche Repo, auch nicht auf `dev`. Deshalb alle elf markierten Tests aus `test_drawing.py`, `test_text.py`, `test_vector.py`, `test_tolerances.py` und `test_export.py` samt ihrer Helfer nach `tests/test_bct.py` verschieben. Die Datei steht in `.gitignore`, Abu gibt sie lokal weiter. `test_bct_marker_skips_without_data` in `test_smoke.py` enthält keine Maße und bleibt. Prüfen: `git grep -n "mark.bct" -- tests` trifft nur noch `test_smoke.py`. Gibt BCT frei, nimmt WP-18 die Datei ins Repo.
6. **`Dockerfile`** so, dass eine Codeänderung die Abhängigkeiten nicht neu installiert (geprüft: Neubau nach Codeänderung 1,3 s statt 2,5 min, Suite grün):
   ```
   FROM python:3.12-slim
   RUN apt-get update \
       && apt-get install -y --no-install-recommends libgl1 libglu1-mesa libxrender1 poppler-utils \
       && rm -rf /var/lib/apt/lists/*
   WORKDIR /app
   # Dependencies first, against an empty package: a code change then reuses this layer.
   COPY pyproject.toml /app/
   RUN mkdir -p /app/d2c && touch /app/d2c/__init__.py && pip install --no-cache-dir -e "/app[dev]"
   COPY . /app
   CMD ["python", "-m", "d2c.server"]
   ```
7. **`.gitignore`** im Wurzelordner (ersetzt die des Prototyps), genau dieser Inhalt:
   ```
   # BCT sample data and everything derived from it stays out of git
   /data/
   /drawings/
   /tables/
   /results/
   /tests/test_bct.py
   out/
   .env
   __pycache__/
   *.pyc
   *.egg-info/
   .pytest_cache/
   .venv/
   .DS_Store
   *.zip
   /docs/team/
   ```
8. **`.dockerignore`** neu anlegen (das Archiv enthält keine). Ohne sie kopiert `COPY . /app` die BCT-Daten und `.env` ins Image:
   ```
   .git/
   .env
   data/
   drawings/
   tables/
   results/
   out/
   prototype/
   docs/team/
   **/__pycache__/
   .pytest_cache/
   .venv/
   *.zip
   ```
9. **`.gitattributes`** für Windows-Rechner:
   ```
   * text=auto eol=lf
   *.pdf binary
   *.png binary
   *.jpg binary
   *.glb binary
   d2c/web/static/vendor/** -text
   ```
10. **Vorläufiges `README.md`** (Englisch, das endgültige schreibt WP-14): Titel „Drawing2CAD“, ein Satz („An AI reads a 2D technical drawing and builds a checked STEP model through a local MCP server.“), dazu die drei Befehle `docker build -t d2c .`, `docker run --rm d2c pytest -q` und der Hinweis, dass `prototype/` den ersten Prototyp enthält.
11. **Bauen und testen:**
    - `docker build -t d2c .`
    - `docker run --rm --network none -v "$PWD":/app d2c pytest -q -p no:cacheprovider tests/` (Windows PowerShell: `-v "${PWD}:/app"`)
    - `docker run --rm d2c pytest -q` (ohne eingehängten Ordner, so wie der KI-Prüfer: darf keine BCT-Datei finden)
12. **Spuren prüfen**, alle drei Ausgaben ohne Treffer bzw. nur Teammitglieder:
    - `git log --all --format='%an <%ae>%n%cn <%ce>' | sort -u`
    - `git log --all --format=%B | grep -i -E 'co-authored|code/session_|generated with'`
    - `git grep -n -i -E 'desktop|opus|co-authored|generated with|code/session_'`
13. Commit `chore: adopt the d2c core, move the first prototype to prototype/`, Push auf `dev`, Klon-Befehl in den Team-Chat. Kristian lädt alle als Collaborator ein.
14. **Daten lokal** (jeder selbst, nie committen): `data/`, `drawings/`, `tables/` mit den Befehlen aus `spec.md` Abschnitt 5.9, dazu `tests/test_bct.py` von Abu.

## Abnahme

- Auf `dev` ohne `tests/test_bct.py` (so sieht es der KI-Prüfer): 239 grün, 1 übersprungen (der Marker-Test). Mit `tests/test_bct.py` und `data/`: alle 254 grün.
- `docker run --rm d2c pytest -q` ist grün und das Image enthält weder `data/` noch `.env` (`docker run --rm d2c ls -a /app` zeigt sie nicht).
- `git grep -n "mark.bct" -- tests` trifft nur `test_smoke.py`.
- Spuren-Prüfung aus Schritt 12 ohne Treffer, falls die Bereinigung beschlossen wurde. Sonst ist der Zustand im Team-Chat dokumentiert.
- Jedes Teammitglied hat geklont, gebaut und einmal getestet.

## Fallen

- Docker auf dem Mac: OrbStack oder Docker Desktop muss laufen. Unter Windows: Docker Desktop mit WSL2-Backend, vor dem Klonen `git config --global core.autocrlf false`.
- Die BCT-Daten enthalten einen Ordner mit Umlaut (`Trägerplatte`). Auf dem Mac liegt er nach dem Download zerlegt kodiert (NFD) vor. Prüfung und Umbenennung: `spec.md` Abschnitt 5.9.
- `Archiv.zip` enthält BCT-Daten. Die Datei nie ins Repo legen, nie weitergeben. Abu kopiert nur die Code-Ordner daraus.
- Wer `[tool.setuptools] packages` in `pyproject.toml` auf eine feste Liste mit Unterpaketen setzt, bricht den Build aus Schritt 6 (beim Installieren existiert nur `d2c/__init__.py`). WP-08 stellt deshalb auf `packages.find` um.
