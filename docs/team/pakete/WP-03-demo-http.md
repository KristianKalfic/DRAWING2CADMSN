# WP-03: Demo-Befehl (Teil 1) und MCP über HTTP (Teil 2)

| Teil | Profil | Aufwand | Braucht vorher | Gate |
|---|---|---|---|---|
| 1 Demo-Befehl | C (DevOps) oder A | 1 h | WP-00 | G2, Sa 15:00 |
| 2 MCP über HTTP | A (Core) | 0,5 h | WP-01 | G3, Sa 20:00 |

Die beiden Teile sind unabhängig und können von zwei Personen gleichzeitig bearbeitet werden.

## Ziel

1. Ein Befehl beweist ohne LLM, ohne Netz und ohne BCT-Daten, dass die ganze Kette läuft. Das ist der Nachweis „läuft“ für den KI-Prüfer und der dritte Befehl im README-Quickstart (J1).
2. Der MCP-Server spricht zusätzlich HTTP, damit Agent und Web-Oberfläche in Docker Compose über ein internes Netz mit ihm reden (WP-06, WP-08).

## Kontext

- `Session` (`d2c/session.py`): `submit_spec(spec)`, `apply(part, op)`, `export(part)`. Ergebnisordner aus `D2C_RESULTS`, bei jedem Aufruf neu gelesen.
- Demo-Fixtures existieren: `tests/fixtures/demo_spec.json` (Teil `demo_bracket`, angenommene Dicke `t10` mit Review `rv1`) und `tests/fixtures/demo_ops.json` (vier Ops). `tests/test_server.py::test_demo_end_to_end` zeigt den Ablauf über die Tools.
- Der Server startet heute mit `server.run("stdio")`. In mcp 2.3 nimmt `run("streamable-http", host=..., port=...)` die Optionen von `run_streamable_http_async` an (`host` Standard `127.0.0.1`, `port` Standard 8000, Pfad `/mcp`).

## Dateien

- Teil 1: `d2c/demo.py`, `tests/test_demo.py`.
- Teil 2: in `d2c/server.py` nur der Startteil (`if __name__ == "__main__"`), in `tests/test_server.py` nur der HTTP-Test.

## Aufgaben

**Teil 1: `python -m d2c.demo [--out DIR]`** (Standard `DIR` = `/tmp/d2c-demo`)

1. `D2C_RESULTS` auf `DIR` setzen, bevor die `Session` entsteht. `D2C_TABLES` auf `tests/fixtures/tables` setzen, falls die Variable fehlt (öffentliche ISO-Werte im BCT-Format; WP-02 braucht sie für die Toleranzen).
2. Die beiden Fixtures relativ zum Paket laden: `Path(__file__).resolve().parents[1] / "tests" / "fixtures"`.
3. `submit_spec`, alle Ops nacheinander über `apply`, dann `export`.
4. Ausgabe, in dieser Reihenfolge:
   - je Prüfung eine Zeile `PASS` oder `FAIL` mit Name, Soll und Ist (Solid, jede Zeile aus `dims`, Roundtrip),
   - die Annahmen aus der Review-Liste (id, Maß, Annahme, Grund),
   - die geschriebenen Dateien,
   - zum Schluss `All checks passed: yes` oder `no`.
5. Exit-Code 0 genau dann, wenn der Export `ok` ist.

**Teil 2: `python -m d2c.server [--http HOST:PORT]`**

6. Ohne Option wie bisher stdio. Mit Option streamable HTTP unter `/mcp`, z. B. `--http 0.0.0.0:8765`.
7. Kein eigener Webserver, keine neue Abhängigkeit.

## Tests (zuerst schreiben)

- `test_demo_cli_ok` (Teil 1): Subprozess `python -m d2c.demo --out <tmp>` → Exit-Code 0, `<tmp>/demo_bracket/demo_bracket.step` existiert, stdout enthält `All checks passed: yes` und die Annahme `t10`.
- `test_server_http_lists_tools` (Teil 2): Server als Subprozess mit `--http 127.0.0.1:<freier Port>`, Client: `streamable_http_client(url)` aus `mcp.client.streamable_http` liefert `(read, write)` für `ClientSession(read, write)`; `list_tools` liefert genau `TOOLS` aus derselben Datei. Höchstens 30 s Wartezeit auf den Start, danach den Server beenden. Die Client-API im Container nachlesen, nicht raten.

## Abnahme

- `docker run --rm d2c python -m d2c.demo` zeigt nur `PASS`-Zeilen und `All checks passed: yes`. Mit `-v "$PWD/out":/out` und `--out /out` bleiben die Dateien erhalten.
- Beide Tests grün, komplette Suite grün.
- Teil 2: `docker run --rm -p 8765:8765 d2c python -m d2c.server --http 0.0.0.0:8765` startet, ein MCP-Client auf dem Host sieht unter `http://localhost:8765/mcp` die Tools.

## Fallen

- **Host-Header-Schutz:** Bindet der Server an `127.0.0.1` oder `localhost`, schaltet mcp 2.3 automatisch einen Schutz ein, der nur `Host: 127.0.0.1` und `localhost` zulässt. In Compose ruft der Agent `http://mcp:8765/mcp` auf und würde abgewiesen. Im Container deshalb immer an `0.0.0.0` binden. Der Server-Container hat ohnehin kein Netz nach außen.
- Die Fixtures liegen unter `tests/`. Das Image kopiert das Repo nach `/app`, deshalb funktioniert der Pfad dort. Für den Hackathon reicht das; als Paketdaten würden sie erst bei einer Installation außerhalb des Repos gebraucht.
- Die Demo braucht die Zeichnung nicht: `spec.drawing` ist nur ein Text, gebaut wird aus den Ops.
