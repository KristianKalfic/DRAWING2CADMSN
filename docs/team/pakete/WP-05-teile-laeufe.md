# WP-05: Isolierte Teile-Läufe einrichten

| Profil | Aufwand | Braucht vorher | Gate |
|---|---|---|---|
| B (KI und Agent) | 45 min | WP-00 | G1, Sa 13:00 |

## Ziel

Jedes Teammitglied kann ein BCT-Teil von einem KI-Modell umsetzen lassen. Die Sitzung sieht **nur** die MCP-Tools: keine Datei-Tools, keine Shell, keine Websuche, keine Referenz-STEPs. Nur so ist der Benchmark ehrlich (R3, „Referenzen nur zum Vergleich“).

## Kontext

- Der Server läuft als Docker-Container mit `--network none` und sieht nur `drawings/` (read-only), `tables/` (read-only) und `results/`. Kein Tool liest andere Dateien. Pfade außerhalb von `/data` und Dateien, die keine PDF sind, lehnt der Server ab.
- Der Server-Code des Kerns ist getestet. Die Läufe warten nicht auf das Review in WP-01. Die Session speichert `spec.json` und `ops.json` nach jedem Schritt, `scripts/replay.py` (WP-04) baut daraus später neu.
- Drei Wege, ein Teil zu rechnen. Alle gehen nur über MCP:
  1. **Desktop-MCP-Client** (Mac oder Windows) mit genau der Server-Konfiguration unten. Für diese Läufe alle anderen MCP-Server und eingebauten Werkzeuge des Clients ausschalten (Dateizugriff, Websuche, Code-Ausführung). Ein neuer Chat pro Teil, keine Dateien oder Bilder anhängen.
  2. **Headless-Client per Skript:** Abu hat eine erprobte Vorlage (`~/d2c-run/run_part.sh`), die nur die MCP-Tools zulässt und das Protokoll als JSON-Zeilen schreibt.
  3. **Eigener Agent** ab G3 (WP-06), über Compose: `docker compose run --rm app python -m d2c.agent "<pdf>" --part <teil> --server http://mcp:8765/mcp`. Dieser Weg liefert die Zahlen, die Jury und KI-Prüfer selbst nachvollziehen können.
- Bis der Agent steht (G3), gibt es nur Weg 1 und 2. Wer keinen eigenen Zugang zu einem Vision-Modell hat, übernimmt bis dahin QA statt Läufe (README Abschnitt 8, Entscheidung 8).
- Kosten: Desktop-Clients laufen über den Zugang der jeweiligen Person. Der Team-Key (OpenRouter, 13,33 $) ist die Reserve für den Agenten.

## Dateien

Nichts im Repo. Jeder legt sich `~/d2c-run/` auf dem eigenen Rechner an. Die Dateien dort nennen Werkzeuge und Pfade des eigenen Rechners.

## Aufgaben

1. **Stand einfrieren:** nach grüner Suite `docker tag d2c d2c:prod`. Teile-Läufe nutzen immer `d2c:prod`, damit laufende Code-Änderungen sie nicht stören. Ein neues `prod` nur nach grüner Suite und Ansage im Team-Chat.
2. **Server-Konfiguration** `~/d2c-run/mcp.json`. `<REPO>` ist der absolute Pfad zum Repo-Ordner, unter Windows mit Schrägstrichen, z. B. `C:/Users/name/DRAWING2CADMSN`:
   ```json
   {"mcpServers": {"drawing2cad": {"command": "docker", "args": [
     "run", "-i", "--rm", "--network", "none",
     "-v", "<REPO>/drawings:/data:ro", "-v", "<REPO>/tables:/tables:ro",
     "-v", "<REPO>/results:/app/results", "d2c:prod"]}}}
   ```
   Desktop-Clients übernehmen diesen Block in ihre eigene Konfigurationsdatei.
3. **Auftrag an das Modell** (erste Nachricht, Englisch, für alle drei Wege gleich):
   „Convert the drawing `<pdf relativ zu drawings/>` into a validated STEP model following the server instructions. Use part name `<teil>`. Finish only after export_step returns ok: true.“
4. **Protokoll:** Headless und Agent schreiben `~/d2c-run/logs/<teil>.jsonl`. Beim Desktop-Client den Chat am Ende exportieren oder mindestens Modell, Dauer und Anzahl Tool-Aufrufe notieren. Diese Werte braucht WP-13 für `docs/results.md`.
5. **Rauchtest** mit der Demo-Zeichnung: `tests/fixtures/demo_drawing.pdf` nach `drawings/Demo/demo_drawing.pdf` kopieren, dann ein Lauf mit Teil `demo_run`. Erwartung: `export_step` liefert `ok: true`, `results/demo_run/review.md` nennt die angenommene Plattendicke.
6. **Isolationstest:** In einer Sitzung `render_drawing` mit `pdf="Fuss/Fuss.stp"` aufrufen lassen. Erwartung: abgewiesen. Die Referenzdateien liegen ohnehin nicht in `drawings/`.
7. Kurze Anleitung in den Team-Chat: wie man ein Teil startet, wohin das Protokoll geht, wie die Ergebnisse zur Sammelstelle kommen.

## Teilnamen und PDF-Pfade

Teilname = Ordnername ohne Leerzeichen, Klammern und Umlaute. Wer ein Teil startet, trägt es vorher im Team-Chat ein, damit niemand dasselbe Teil doppelt rechnet.

| Teilname | PDF relativ zu `drawings/` |
|---|---|
| `Sensoraufnahme` | `Sensoraufnahme (optional PMI)/Sensoraufnahme.pdf` |
| `Fuss` | `Fuss/Fuss_dwg.pdf` |
| `Welle` | `Welle/Welle_dwg.pdf` |
| `Achse` | `Achse/Achse.pdf` |
| `Schnittmenge` | `Schnittmenge/Schnittmenge.pdf` |
| `3-Loch-Hebel` | `3-Loch-Hebel/3-Loch-Hebel.pdf` |
| `Haken` | `Haken/Haken_dwg.pdf` |
| `Rotation` | `Rotation/Rotation_dwg.pdf` |
| `Flansch` | `Flansch/Flansch_dwg.pdf` |
| `Traegerplatte` | `Trägerplatte/Trägerplatte_dwg.pdf` |
| `Dreistern` | `Dreistern/Dreistern_dwg.pdf` |
| `Befestigung` | `Befestigung/Befestigung_dwg.pdf` |
| `Maschinenteil` | `Maschinenteil/Maschinenteil_dwg.pdf` |
| `Getriebedeckel` | `Getriebedeckel (optional PMI)/Getriebedeckel.pdf` |
| `Lagerschaft` | `Lagerschaft/Lagerschaft.pdf` |
| `Fixierung` | `Fixierung/Fixierung.pdf` |
| `Splint` | `Splint/Splint.pdf` |
| `Grundplatte` | `Grundplatte/Grundplatte.pdf` |
| `Komplexe_Zeichnung_3` | `Komplexe Zeichnung 3/1 763 1487 0.pdf` |
| `Komplexe_Zeichnung_1` | `Komplexe Zeichnung 1/1 106 0168 3.pdf` |
| `Komplexe_Zeichnung_2` | `Komplexe Zeichnung 2/1 124 0146 0.pdf` |

## Sammelstelle

Die Ergebnisse entstehen auf mehreren Rechnern. Benchmark (`eval_refs.py`) und Ergebnistabelle brauchen sie an einem Ort: auf dem Rechner der QA-Person (WP-13). Übergabe von `results/<teil>/` nur lokal (AirDrop, USB-Stick oder Kabel), nie über Chat, Cloud oder Repo, denn die Dateien enthalten BCT-Maße. Es reichen `spec.json` und `ops.json`, den Rest baut `replay.py --export` neu.

## Abnahme

- Rauchtest grün auf mindestens zwei Rechnern, Isolationstest abgewiesen.
- Anleitung im Team-Chat.

## Fallen

- Höchstens drei Teile gleichzeitig pro Rechner (eigene Container, eigene Teilnamen). Mehr bremst Docker auf dem Laptop aus.
- Gleicher Teilname auf zwei Rechnern ergibt zwei verschiedene Ergebnisse. Deshalb vorher im Team-Chat eintragen.
- Ein Desktop-Client startet pro Chat einen eigenen Container. Die Session lädt Spec und Ops eines Teils von der Platte, ein neuer Chat kann also am selben Teil weiterbauen, solange der Teilname gleich bleibt.
