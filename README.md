# Drawing2CAD

Drawing2CAD erzeugt aus einer technischen 2D-Zeichnung ein geprüftes 3D-Volumenmodell. Ein Sprachmodell liest die Zeichnung und baut das Teil ausschließlich über die Werkzeuge eines lokalen MCP-Servers. Der Server prüft jeden Bauschritt, exportiert das Modell als STEP und markiert jede Annahme farbig im Modell.

## Schnellstart

```
docker build -t d2c .
docker run --rm d2c pytest -q
docker run --rm d2c python -m d2c.demo
```

Die Demo baut ein Beispielteil ohne Sprachmodell und zeigt jede Prüfung mit Soll und Ist.

## MCP-Server

Der Server spricht MCP über stdio und läuft ohne Netzwerk:

```
docker run -i --rm --network none \
  -v "$PWD/drawings":/data:ro \
  -v "$PWD/results":/app/results \
  d2c
```

`drawings/` enthält die PDF-Zeichnungen, `results/` nimmt die Ergebnisse auf. Unter Windows PowerShell steht `${PWD}` statt `$PWD`. Über HTTP: `python -m d2c.server --http 0.0.0.0:8765`, Endpunkt `/mcp`.

## Web-Oberfläche: Zeichnung hochladen, Modell wählen, STEP erstellen

Auf dem eigenen Rechner mit Claude Code (Docker und [uv](https://docs.astral.sh/uv/) vorausgesetzt):

```
./start.sh
```

Die Web-Oberfläche nutzt dann die Anmeldung von Claude Code, ein Schlüssel ist nicht nötig. Das CAD-Werkzeug läuft je Lauf in einem Docker-Container ohne Netz.

Alles in Containern (Claude dann mit `CLAUDE_CODE_OAUTH_TOKEN` oder `ANTHROPIC_API_KEY` in `.env`):

```
docker compose up --build
```

In beiden Fällen `http://localhost:8080` öffnen:

1. Zeichnung (PDF, PNG oder JPG) ablegen und einen Teilnamen eingeben.
2. Modell auswählen.
3. „STEP erstellen“ klicken. Der Lauf erscheint live; danach stehen STEP-Datei, 3D-Ansicht und Prüfliste bereit.

Die Auswahl bietet:

- **Claude** (Sonnet, Opus, Haiku): mit `./start.sh` über die Anmeldung von Claude Code, im Container mit `CLAUDE_CODE_OAUTH_TOKEN` aus `claude setup-token` oder mit `ANTHROPIC_API_KEY` in `.env`. Findet die Web-Oberfläche keine Anmeldung, ist Claude in der Auswahl gesperrt.
- **Ollama:** alle lokal installierten Modelle. Ollama muss mit großem Kontext laufen: `OLLAMA_CONTEXT_LENGTH=64000 ollama serve`.
- **OpenRouter oder ein anderer Anthropic-kompatibler Endpunkt:** `D2C_LLM_BASE_URL`, `D2C_LLM_API_KEY` und `D2C_LLM_MODEL` in `.env`.

Alle Variablen stehen in `.env.example`. Der MCP-Server läuft in einem eigenen Container ohne Internetzugang; nur die Web-App erreicht ihn. Die Ergebnisse liegen unter `results/<teil>/`.

## Dokumentation

- [Systemspezifikation](docs/spec.md)
- [Architektur](docs/architektur.png)

## Aufbau

| Pfad | Inhalt |
| --- | --- |
| `d2c/` | MCP-Server, Datenmodell, Bau, Prüfungen, Export, Web-Ansicht |
| `tests/` | Tests (pytest) |
| `scripts/` | Erzeugung der Demo-Zeichnung |
| `prototype/` | erster Prototyp: JSON wird zu CadQuery-Code |
