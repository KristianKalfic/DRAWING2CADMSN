# WP-07: Lokale Modelle messen

| Profil | Aufwand | Braucht vorher | Gate |
|---|---|---|---|
| A (Core), auf Abus Mac mit den lokalen Modellen | 2 h | WP-06 | G4, Sa 24:00 |

## Ziel

Messen, was lokal läuft (R9, Datenhoheit): zuerst die Bauphase, dann die Lesephase mit einem lokalen Modell mit Bildeingabe. Nur wenn beide lokal laufen, verlässt die Zeichnung das Haus nicht. Ehrliche Zahlen für README und Pitch, auch wenn die Lesephase lokal scheitert.

## Kontext

Schon geprüft (17.10.): Ollama `/v1/chat/completions` mit `tools` liefert korrekte `tool_calls`, auch mit verschachtelten Objekt-Parametern. Gemessen mit `qwen3.5:4b-mlx` (12 s) und `qwen3.8:27b-mlx` (25 s). Installiert ist außerdem `qwen3.6:35b-mlx`. Abus Rechner hat 36 GB RAM.

## Dateien

`docs/models.md`.

## Aufgaben

1. Ollama auf einem Rechner mit mindestens 16 GB RAM starten (Abus Mac hat 36 GB und die Modelle oben schon installiert), Modelle bereit halten.
2. **Bauphase lokal** am Demo-Teil, über Compose, damit der Server-Container ohne Netz bleibt. Die fertige Demo-Spec ersetzt die Lesephase (`--spec`, WP-06): `docker compose run --rm app python -m d2c.agent "Demo/demo_drawing.pdf" --part demo_local --spec tests/fixtures/demo_spec.json --model <lokales-modell> --no-script --base-url http://host.docker.internal:11434/v1 --server http://mcp:8765/mcp`.
3. Dasselbe mit einem einfachen BCT-Teil, dessen Spec schon aus einem früheren Lauf existiert: `--spec /app/results/Fuss/spec.json --part Fuss_local`.
4. **Lesephase lokal:** ein lokales Modell mit Bildeingabe (`ollama show <modell>` nennt dann `vision` unter Capabilities), Demo-Teil ohne `--spec`, `--build-model` wie in Schritt 2. Drei Läufe, Ergebnis auch bei Misserfolg.
5. Je Modell und Phase drei Läufe. Notieren: Modell, Phase, Läufe ok/gesamt, mittlere Dauer, Auffälligkeiten (z. B. falsche Ebene, Endlosschleife).
6. `docs/models.md` (Englisch): Tabelle `model | phase | runs ok/total | mean duration | notes` plus zwei Sätze Fazit. Nur gemessene Werte, nichts schätzen.

## Abnahme

- Mindestens ein lokales Modell mit mindestens einem erfolgreichen Lauf am Demo-Teil, oder ein ehrliches Ergebnis „nicht erfolgreich, weil …“.
- `docs/models.md` gemergt, Zahlen an WP-17 (Pitch) gemeldet.

## Fallen

- Aus dem Container erreicht man Ollama auf dem Mac über `host.docker.internal`.
- Kleine Modelle verlieren sich leicht in Wiederholungen. Der Agent bricht nach dreimal demselben fehlschlagenden Aufruf ab. Das ist ein Ergebnis, kein Fehler.
