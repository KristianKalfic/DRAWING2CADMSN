# WP-14: README, Architektur, CI, Frisch-Klon-Prüfung

| Teil | Profil | Aufwand | Braucht vorher | Gate |
|---|---|---|---|---|
| 1 CI | C (Frontend und DevOps) | 20 min | WP-00 | G1, Sa 13:00 |
| 2 README, Architektur, Frisch-Klon-Prüfung | C | 2,5 h | WP-03 Teil 1, WP-08; Zahlen aus WP-13 | G4, Sa 24:00 (Zahlen bis G5 nachtragen) |

## Ziel

Die Hälfte der Wertung vergibt eine KI, die Code, Architektur und Baubarkeit prüft und das Repo nach README baut. Dieses Paket entscheidet, ob sie es schafft und ob sie jede Anforderung findet (J1, J2, J3).

## Dateien

`README.md`, `docs/architecture.md`, `docs/img/architecture.excalidraw`, `docs/img/architecture.svg`, `docs/results.md` (Gerüst, Inhalt aus WP-13), `docs/models.md` (Gerüst, Inhalt aus WP-07), `.github/workflows/ci.yml`, `LICENSE`. Nur prüfen: `.env.example` (WP-06), `.dockerignore` (WP-00).

## Aufgaben

**Teil 1 (bis G1): CI**

- `.github/workflows/ci.yml`: bei `push` und `pull_request`, `ubuntu-latest`, Schritte `docker build -t d2c .` und `docker run --rm d2c pytest -q`. Ab dann gilt die Merge-Regel „CI grün“ (README Abschnitt 7). Demo und Compose kommen in Teil 2 dazu.

**Teil 2 (bis G4): README, Architektur, Frisch-Klon-Prüfung**

1. `README.md` (Englisch, sachlich, ohne Werbesprache), in dieser Reihenfolge:
   1. Ein Satz Zweck, ein Bild (`docs/img/web-ui.png` aus WP-08).
   2. **Quickstart**, jeder Befehl im frischen Klon geprüft, für Linux und Mac (Windows PowerShell: `${PWD}` statt `$PWD`):
      - `docker build -t d2c .`
      - `docker run --rm d2c pytest -q` (ein Test wird übersprungen: er braucht die BCT-Beispieldaten)
      - `docker run --rm d2c python -m d2c.demo`
      - `docker compose up -d`, dann `http://localhost:8080` (Prüfung: `curl -fsS localhost:8080/ | head -5`), am Ende `docker compose down`. Das Demo-Teil erscheint nach `docker compose run --rm app python -m d2c.demo --out /app/results`. Für neue Zeichnungen einen LLM-Endpunkt in `.env` eintragen (Vorlage `.env.example`).
      - als MCP-Server für jeden MCP-Client: `docker run -i --rm --network none -v "$PWD/drawings":/data:ro -v "$PWD/results":/app/results d2c`, mit dem Satz „For MCP clients: the container waits for a client on stdin.“
   3. **How it works:** Architekturbild und die Pipeline in fünf Schritten. Vorlage ist das private `architektur.excalidraw` aus dem Teamplan (liegt neben `spec.md`): Texte auf Englisch, Stand-Farben entfernen (alle Module in einer Farbe), WP-Nummern entfernen. Ablegen als `docs/img/architecture.excalidraw`, auf excalidraw.com öffnen (nur lokal, keinen Teilen-Link erzeugen) und als `docs/img/architecture.svg` exportieren.
   4. **MCP tools:** Tabelle Name, Zweck, wichtigste Parameter (aus `spec.md` 5.4, auf Englisch).
   5. **Validation and review:** Auszug aus der Review-Liste des Demo-Teils, Bedeutung der Farben. Ein BCT-Teil (Sensoraufnahme) nur, wenn BCT die Veröffentlichung freigegeben hat (WP-16).
   6. **Results:** Tabelle aus `docs/results.md`.
   7. **Requirement coverage:** Matrix R1–R12, M1–M3, C1–C2, X1, J1–J3 → Modul → Testdatei und Testfunktion (die es wirklich gibt, per `grep` prüfen) → Beleg.
   8. **Model independence:** getestete Modelle aus `docs/models.md`.
   9. **Using it from Windows:** Docker Desktop mit WSL2, ein Desktop-MCP-Client, Konfigurationsblock wie in WP-05 (`mcp.json`, Pfade mit Schrägstrichen).
   10. **Security:** Sandbox-Schichten, kein Netz im Server-Container, keine Secrets im Repo, BCT-Daten nie im Image (`.dockerignore`).
   11. **Limitations:** ehrlich, mit konkreten Teilen aus WP-12. Dazu: gemessen nur an Vektor-PDFs; Scans und Bilder laufen ohne Vektormessung (`spec.md` 2.1).
   12. **Project layout**, **Running with the BCT sample data** (Ordner `data/`, `drawings/`, `tables/` anlegen, Aufbau aus `spec.md` 5.9), **History** (ein Satz: Der erste Prototyp ist über den Tag `prototype-v0` erhalten, WP-18).
   13. **Licenses:** PyMuPDF ist AGPL-3.0, das Projekt erbt diese Pflichten bei Weitergabe; model-viewer ist Apache-2.0.
2. `docs/architecture.md`: dasselbe SVG, Schichten, Datenfluss, die zwölf Kernentscheidungen aus `spec.md` Abschnitt 4 (auf Englisch), Sicherheitsmodell.
3. `docs/results.md` und `docs/models.md`: Tabellenkopf und der Satz „Results are filled in after the benchmark runs.“ Keine erfundenen Zahlen.
4. `LICENSE`: AGPL-3.0-or-later (passend zu PyMuPDF), nur falls keine existiert.
5. `.github/workflows/ci.yml` aus Teil 1 ergänzen: `docker run --rm d2c python -m d2c.demo` und `docker compose config`.
6. **Frisch-Klon-Prüfung:** in ein leeres Verzeichnis (`mktemp -d`) klonen, alle Quickstart-Befehle ausführen. Zusätzlich auf dem Mac `docker buildx build --platform linux/amd64 -t d2c-amd64 --load .` und `docker run --rm --platform linux/amd64 d2c-amd64 pytest -q`. `docker run --rm d2c ls -a /app` zeigt weder `data` noch `drawings` noch `.env`. `docker compose up` muss starten und `/` ausliefern (`curl -s localhost:8080 | head`), dann `docker compose down`.
7. **Spuren-Prüfung**, Ausgaben leer bzw. nur die vier Teammitglieder:
   - `git grep -n -i -E 'sk-or-|co-authored|generated with|code/session_'` (nur `code/session_`: Testnamen wie `test_session_export` sind kein Befund)
   - `git grep -n -E "(api_key|API_KEY) *= *['\"][^'\"]{12,}"` (ein Key als Text im Code; `api_key=None` in Signaturen ist kein Befund)
   - `git ls-files | grep -E '\.(stp|step|prt|jt|zip)$'` und `git ls-files tests/test_bct.py` (beide leer, außer mit BCT-Freigabe)
   - `git log --format='%an <%ae>%n%cn <%ce>' | sort -u`
   - Sichtprüfung von README und `docs/`: keine Namen von KI-Assistenten oder Entwicklungswerkzeugen. Modellnamen in `docs/models.md` sind fachlich und bleiben.

## Abnahme

- Frisch-Klon-Prüfung grün auf arm64 (Mac) und amd64, CI grün auf GitHub.
- Jede Zeile der Anforderungsmatrix nennt eine existierende Testfunktion.
- Spuren-Prüfung ohne Befund.

## Fallen

- Der KI-Prüfer hat keine BCT-Daten und keinen Key. Alles im Quickstart muss ohne beides laufen. Die BCT-Tests liegen nicht im Repo (WP-00), nur der Marker-Test wird übersprungen.
- `docker run -v ./ordner:...` mit relativem Pfad versteht nicht jede Docker-Version. Im README immer `"$PWD/ordner"` schreiben.
- `docs/team/` (der private Teamplan) ist nie im Repo. Wer README-Texte daraus übernimmt, übersetzt sie und lässt interne Namen, Gates und WP-Nummern weg.
