# WP-06: Modellunabhängiger Agent

| Profil | Aufwand | Braucht vorher | Gate |
|---|---|---|---|
| B (KI und Agent) | 4 h (inklusive Einarbeitung in den MCP-Client) | WP-01; der HTTP-Weg erst nach WP-03 Teil 2 | G3, Sa 20:00 |

## Ziel

Der Server ist schon modellunabhängig. Der Agent zeigt es: Er steuert den Server mit jedem OpenAI-kompatiblen Endpunkt (OpenRouter, Ollama, LM Studio, vLLM) und trennt Lese- und Bauphase (R9). Er ist außerdem der Motor für Live-Läufe in der Web-Oberfläche (WP-08).

## Kontext

- Server mit 14 Tools (WP-01), stdio sofort, HTTP nach WP-03 Teil 2. Bis dahin entwickelt und testet der Agent nur über stdio.
- `mcp==2.3.0` bringt den MCP-Client und **`httpx2`** mit (Modulname `httpx2`, API wie httpx). `httpx` selbst ist **nicht** installiert, `import httpx` scheitert. **Keine neue Abhängigkeit.** Die Client-API im installierten Paket nachlesen, nicht raten: `mcp.client.stdio` (`stdio_client`, `StdioServerParameters`), `mcp.client.streamable_http` (`streamable_http_client(url)`), `mcp.client.session.ClientSession`. In mcp 2.3 heißen die Attribute `tool.input_schema`, `result.is_error` und `result.structured_content`.
- Ereignisformat und Aufrufvertrag: `spec.md` Abschnitt 5.7. Die Web-App (`d2c/web/app.py`) existiert schon und ruft `run_agent` genau so auf: alle Argumente als Schlüsselwörter, `on_event` als normale Funktion. Ihr Test `test_run_streams_events` nutzt einen Fake-Agenten mit derselben Signatur.

## Dateien

`d2c/agent.py`, `tests/test_agent.py`, `.env.example`.

## Aufgaben

1. `async def run_agent(pdf, part, *, model, base_url, api_key=None, build_model=None, server="stdio", spec=None, allow_script=True, max_turns=80, on_event=None) -> dict`
   - `on_event` ist eine normale Funktion (kein `async`), `None` heißt: keine Ereignisse. Ruft sie mit jedem Ereignis auf, zuletzt mit `done`.
   - `server`: `"stdio"` startet `python -m d2c.server` als Subprozess über `StdioServerParameters(command=sys.executable, args=["-m", "d2c.server"], env=...)`. Der stdio-Client gibt sonst nur `HOME`, `LOGNAME`, `PATH`, `SHELL`, `TERM` und `USER` weiter. `env` enthält deshalb alle `D2C_`-Variablen außer `D2C_LLM_*`, der API-Key geht nie an den Server. Eine URL `http://…/mcp` verbindet per HTTP. Ein String, der mit `docker ` beginnt, wird mit `shlex.split` zerlegt und als stdio-Befehl gestartet.
   - Systemprompt = `instructions` aus der MCP-Initialisierung plus drei Regeln: nur über Tools handeln; nach jedem Report die Regeln der Anleitung anwenden; fertig erst nach `export_step` mit `ok: true`.
   - Tools: `list_tools` → OpenAI-Format `{"type": "function", "function": {"name", "description", "parameters"}}`. `parameters` ist `tool.input_schema` mit aufgelösten `$ref`/`$defs` über `inline_refs(schema)`. Viele Endpunkte und kleine Modelle verarbeiten `$ref` schlecht. `inline_refs` erhält `anyOf`/`oneOf`, entfernt `discriminator` (dessen `mapping` zeigt auf `#/$defs/…` und liefe nach dem Auflösen ins Leere) und erkennt Zyklen (`ValueError` mit dem Namen der Referenz).
   - Anfrage per `httpx2.AsyncClient` an `<base_url>/chat/completions`, `temperature` 0, `tool_choice` `"auto"`, Header `Authorization: Bearer <api_key>` nur wenn gesetzt.
   - Tool-Ergebnisse: Text → Tool-Nachricht. Bild → Tool-Nachricht „image attached in the next message“, danach User-Nachricht mit `{"type": "image_url", "image_url": {"url": "data:image/png;base64,…"}}`.
   - Ungültiges JSON in Argumenten oder ein Ergebnis mit `is_error` → Tool-Nachricht mit dem Fehlertext, die Schleife läuft weiter.
   - Zwei Phasen: Ist `build_model` gesetzt und `submit_spec` erfolgreich, beginnt ein neuer Verlauf mit `build_model`. Erste Nachricht: Teilname und eingereichte Spec als JSON. Nur Bau-Tools, also ohne `render_drawing`, `read_drawing_text`, `measure_drawing`. In der Bauphase gehen keine Bilder durch.
   - `spec` (dict oder `None`): Ist sie gesetzt, entfällt die Lesephase. Der Agent setzt `name` auf `part`, reicht sie selbst per `submit_spec` ein und startet direkt die Bauphase. So misst WP-07 die Bauphase allein, mit nur einem Endpunkt.
   - `allow_script=False` nimmt `run_script` aus der Tool-Liste. Für kleine lokale Bau-Modelle (R7: freier Code nur als Rückfall).
   - Ende: `export_step` mit `ok: true` → `{"ok": true, "part", "turns", "files", "usage"}`. `usage` summiert `prompt_tokens` und `completion_tokens` aller Antworten, soweit der Endpunkt sie liefert; WP-13 rechnet daraus die Kosten je Teil. `max_turns` erreicht → `{"ok": false, "reason"}`. Derselbe Toolname mit denselben (kanonisch sortierten) Argumenten scheitert dreimal → `{"ok": false, "reason": "repeated failing call <name>"}`.
   - `on_event` bekommt `phase`, `tool_call`, `tool_result`, `message` und zuletzt `done`.
2. CLI `python -m d2c.agent <pdf> --part NAME [--model M] [--build-model M2] [--base-url URL] [--server S] [--spec FILE] [--no-script] [--max-turns N] [--events FILE|-]`. Standardwerte aus `D2C_LLM_BASE_URL`, `D2C_LLM_API_KEY`, `D2C_LLM_MODEL`, `D2C_LLM_BUILD_MODEL`. `--events -` gibt eine JSON-Zeile je Ereignis aus. Das Endergebnis immer als JSON auf stdout. Exit-Code 0 nur bei `ok`.
3. `.env.example` mit zwei auskommentierten Varianten: OpenRouter (`https://openrouter.ai/api/v1`) und Ollama lokal (`http://host.docker.internal:11434/v1`). **Kein Wert, der wie ein Key aussieht.**
4. Zeichnungen der Sitzung: Der Agent reicht `pdf` unverändert an die Tools weiter. Die Pfade sind relativ zu `D2C_DATA` des Servers (`spec.md` 5.8), z. B. `Fuss/Fuss_dwg.pdf` oder `uploads/<teil>.pdf` aus der Web-Oberfläche.

## Tests (zuerst schreiben)

Das LLM ist ein Skript über `httpx2.MockTransport` (fertige `chat/completions`-Antworten, Anfragen werden mitgeschrieben). Der Server läuft echt per stdio, `D2C_DATA` und `D2C_RESULTS` zeigen auf tmp.

- `test_inline_refs_removes_refs`: Schema von `extrude` ohne `$ref`, Profil-Varianten erhalten.
- `test_inline_refs_detects_cycle`.
- `test_agent_scripted_run_exports`: `submit_spec` mit der Demo-Spec, die vier Demo-Ops, `export_step` → `ok`; Ereignisse enthalten `tool_call`, `tool_result`, `done`.
- `test_image_result_becomes_user_image`: nach `render_drawing` enthält die nächste Anfrage `data:image/png;base64,`.
- `test_bad_json_arguments_reported`: Argumente `"{not json"` → Tool-Nachricht mit Fehlertext, Lauf geht weiter.
- `test_two_phase_switches_model`: nach `submit_spec` nutzen die Anfragen `build_model` und enthalten keine Lese-Tools.
- `test_stops_after_max_turns`, `test_stops_on_repeated_failing_call`.
- `test_spec_skips_reading_phase`: mit `spec` ruft der erste Tool-Aufruf `submit_spec` auf, keine Lese-Tools in der Anfrage.
- `test_no_script_hides_run_script`.

## Abnahme

- Tests grün, komplette Suite grün, keine Netzwerkzugriffe in Tests.
- Ein echter Lauf über OpenRouter mit dem Demo-Teil (kostet Cent-Beträge), Ergebnis im Team-Chat.

## Betrieb

Echte Läufe immer über Compose (WP-08): `docker compose run --rm app python -m d2c.agent "<pdf>" --part <teil> --server http://mcp:8765/mcp`. So läuft der Server im Container ohne Netz, der Agent im Container mit Netz. `--server stdio` nur für Tests und Entwicklung: Dann läuft der Server im selben Container wie der Agent und hätte dessen Netz.

Server und Session laufen nur im Linux-Container. Unter macOS scheitert `RLIMIT_AS` im Sandbox-Worker, jeder Build bricht dort ab. Den Agenten deshalb nie nativ auf dem Mac mit `--server stdio` starten.

## Fallen

- Den Key nie in Logs ausgeben, auch nicht im Fehlerfall.
- OpenRouter und Ollama liefern `tool_calls` leicht unterschiedlich (z. B. `arguments` als String). Immer als String behandeln und selbst parsen.
