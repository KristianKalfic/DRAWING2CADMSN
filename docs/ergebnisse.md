# Ergebnisse

Messungen mit Drawing2CAD. Die Beispielzeichnungen und Referenzmodelle sind vertraulich und nicht Teil des Repositorys; dieses Dokument nennt deshalb nur aggregierte Werte, keine Teilenamen und keine Maße.

## 1. Lauf über die 22 Beispielzeichnungen mit einem Cloud-Modell

Letzter vollständiger Lauf vom 18.10.2026 mit Claude Sonnet: Jede Zeichnung wurde in einem eigenen Lauf von `python -m d2c.agent.batch` gelesen und gebaut (vier Läufe parallel). Der MCP-Server lief je Lauf als eigener Prozess. Die 22 Zeichnungen umfassen die 21 Beispielteile und die geänderte Revision eines Teils.

| Messgröße | Ergebnis |
| --- | --- |
| Zeichnungen als STEP exportiert | 21 von 22 |
| STEP-Dateien, die in FreeCAD 1.1.4 fehlerfrei öffnen und die Prüfung auf Körper, Größe und Bohrungen bestehen | 21 von 21 |
| Serverprüfungen bestanden (Zeilen aller exportierten Teile) | 350 von 351 |
| Übereinstimmung mit der Referenz in Volumen, Begrenzungsrahmen und Bohrungsdurchmessern | 10 von 21 bewertbaren Teilen |
| Übereinstimmung mit der Referenz im Begrenzungsrahmen | 20 von 21 bewertbaren Teilen |
| Übereinstimmung mit der Referenz im Volumen (höchstens 1 %) | 14 von 21 bewertbaren Teilen |
| Prüfeinträge insgesamt, jeder im Modell markiert | 39 |
| Dauer je Teil im Mittel | rund 3 Minuten |
| Kosten je Teil im Mittel | rund 0,45 USD |

Erläuterungen:

- **Nicht exportiert:** Bei einer Zeichnung brach der Lauf ab, weil eine Antwort der CLI größer als der damalige Nachrichtenpuffer von 1 MB war. Der Puffer ist seither 32 MB groß; ein Einzellauf dieser Zeichnung danach endete mit Export.
- **Serverprüfungen** sind die Prüfungen aus Abschnitt 7 der Spezifikation: gültiger Volumenkörper, Gesamtmaße und Bohrungen gegen die Angaben der Zeichnung, STEP-Rückimport, Rücklesen des PMI. Die eine nicht bestandene Zeile ist ein angenommenes Maß, für das kein PMI entstand; das Teil wurde exportiert, sein Siegel lautet „All checks passed: no“.
- **FreeCAD:** `scripts/freecad_check.py` liest die STEP-Datei unabhängig vom Aufbau in FreeCAD 1.1.4 und prüft gültigen Körper, genau einen Festkörper, Größe und jede erwartete Bohrung mit der Schwelle 0,01 mm. STEP-Leser und Messcode sind unabhängig vom Aufbau, der Geometriekern (OpenCascade) ist derselbe.
- **Referenzvergleich:** `scripts/eval_refs.py` vergleicht jedes exportierte Modell mit dem Referenz-STEP des Teils. Die Referenzen dienen nur der Bewertung; der Server und der Agent sehen sie nie. Ein Teil gilt als übereinstimmend, wenn die Volumenabweichung höchstens 1 % beträgt, der Begrenzungsrahmen höchstens 0,05 mm abweicht und die Bohrungsdurchmesser beider Modelle übereinstimmen (Schwelle 0,01 mm; kein Durchmesser fehlt, keiner kommt hinzu).
- **Streuung:** Das Ergebnis schwankt zwischen Läufen. Ein früherer vollständiger Lauf vom 17.10.2026 auf 21 Zeichnungen ergab 21 von 21 Exporten und 9 von 20 übereinstimmenden Referenzen.

### Ergebnis je Teil

Die Teile sind anonym nummeriert; die Zuordnung zu den Zeichnungen steht nicht im Repository. Die Spalte „Serverprüfungen“ nennt bestandene von ausgeführten Prüfzeilen des Exports, „FreeCAD“ das Ergebnis von `scripts/freecad_check.py` mit der Spezifikation des Teils, „Prüfeinträge“ die Zahl der Annahmen und offenen Punkte in der Prüfliste, „Referenz“ das Ergebnis von `scripts/eval_refs.py`.

| Teil | STEP | Serverprüfungen | FreeCAD | Prüfeinträge | Referenz übereinstimmend |
| --- | --- | --- | --- | --- | --- |
| Teil 01 | ja | 15 von 15 | bestanden | 1 | nein |
| Teil 02 | ja | 15 von 15 | bestanden | 2 | nein |
| Teil 03 | ja | 18 von 18 | bestanden | 2 | ja |
| Teil 04 | ja | 15 von 15 | bestanden | 1 | nein |
| Teil 05 | ja | 19 von 19 | bestanden | 3 | ja |
| Teil 06 | ja | 24 von 24 | bestanden | 1 | ja |
| Teil 07 | ja | 19 von 19 | bestanden | 1 | ja |
| Teil 08 | ja | 19 von 19 | bestanden | 3 | nein |
| Teil 09 | ja | 17 von 17 | bestanden | 5 | nein |
| Teil 10 | ja | 15 von 15 | bestanden | 0 | ja |
| Teil 11 | ja | 18 von 18 | bestanden | 3 | nein |
| Teil 12 | nein | - | - | - | nicht bewertbar |
| Teil 13 | ja | 18 von 18 | bestanden | 4 | nein |
| Teil 14 | ja | 16 von 16 | bestanden | 1 | ja |
| Teil 15 | ja | 16 von 16 | bestanden | 2 | nein |
| Teil 16 | ja | 15 von 15 | bestanden | 1 | ja |
| Teil 17 | ja | 13 von 14 | bestanden | 2 | ja |
| Teil 18 | ja | 14 von 14 | bestanden | 1 | nein |
| Teil 19 | ja | 16 von 16 | bestanden | 3 | nein |
| Teil 20 | ja | 17 von 17 | bestanden | 1 | nein |
| Teil 21 | ja | 19 von 19 | bestanden | 2 | ja |
| Teil 22 | ja | 12 von 12 | bestanden | 0 | ja |

Summen: 21 STEP-Dateien von 22 Zeichnungen, 350 von 351 Prüfzeilen, 21 von 21 in FreeCAD, 39 Prüfeinträge, 10 übereinstimmende Referenzen von 21 bewertbaren.

## 2. Lokale Modelle

Modelle, die lokal über Ollama laufen, werden mit demselben Agenten gemessen. Sie bekommen automatisch den Lite-Modus (kleine Werkzeugsätze je Phase, siehe `docs/spec.md`, Abschnitt 4.6).

### Messverfahren

`scripts/bench_models.py` führt jedes Modell auf jedem Teil im gewählten Modus aus. Jeder Lauf erhält einen frischen Ergebnisordner; der Agent läuft auf dem Rechner, der MCP-Server je Lauf als Docker-Container ohne Netz. Das Skript schreibt jeden Lauf als Zeile einer JSONL-Datei und fasst sie als Markdown-Tabelle mit den Spalten Modell, Teil, Modus, ok, Züge, Aufrufe, Schemafehler, Umdeutungen, Sekunden und Kosten zusammen.

```
python scripts/bench_models.py --models <modell> ... --parts demo <teil> ... --mode full \
    --drawings <zeichnungen> --results <ergebnisse> --out <ordner>
```

Modi:

- `full`: das Modell liest die Zeichnung und baut das Teil.
- `build`: die Spezifikation kommt aus `--results` (Ergebnisordner mit `<teil>/spec.json`), das Modell baut nur.
- `hybrid`: `--read-model` (Standard `sonnet`) liest die Zeichnung, das lokale Modell baut.

Kennzahlen je Lauf:

- **ok:** der Lauf endet mit einem Export, bei dem alle Serverprüfungen bestehen. Ohne `done`-Ereignis (Absturz, Zeitlimit `--timeout`, Standard 1800 Sekunden) gilt der Lauf als gescheitert.
- **Züge:** Zahl der Antworten des Modells.
- **Schemafehler:** Werkzeugaufrufe, die der Server wegen eines Formfehlers der Argumente abweist.
- **Umdeutungen:** Argumente, die die Eingabeschicht (`d2c/server/coerce.py`) korrigiert hat.
- **Aufrufe:** Zahl der Werkzeugaufrufe.
- **Sekunden** und **Kosten** in USD, soweit das Backend sie meldet.

Vor Läufen mit einem lokalen Modell prüft das Skript über die Ollama-Schnittstelle, dass dessen Kontext mindestens `--context` groß ist (Standard 64000); sonst bricht es ab. `--drawings` (Ordner mit einem Unterordner je Teil) und `--results` (Ergebnisordner früherer Läufe) sind Pflichtargumente. Das Teil `demo` ist die Demo-Zeichnung aus `d2c/demo`. `--thinking` begrenzt die Denk-Token lokaler Läufe.

### Ergebnisse

Messung mit der Demo-Zeichnung und drei Beispielteilen (Modus `full`, Ollama, Zeitlimit 1800 Sekunden je Lauf):

| Modell | Demo | Beispielteile | Anmerkung |
| --- | --- | --- | --- |
| qwen 4b | gescheitert | 1 von 3 gebaut, 2 gescheitert | der gebaute Lauf dauerte 776 Sekunden |
| qwen 27b | ok | 1 von 3 ok, 2 Zeitüberschreitung | |

Lokale Modelle bauen die Demo und einfache Teile; bei den meisten Beispielteilen reichen Zeit oder Zuverlässigkeit nicht. Die Stichprobe ist zu klein für eine Aussage über die Qualität der Ergebnisse. Für belastbare Zahlen steht Claude Sonnet (Abschnitt 1).
