# Web-Oberfläche: Gestaltung „Prüfblatt“

Gestaltung und Bedienung der Web-Oberfläche (`d2c/web/static/`: `index.html`, `style.css`, `app.js`). Daten und Endpunkte: `docs/spec.md`, Abschnitt 12.

## Idee

Die Oberfläche ist ein **geprüftes Zeichenblatt**: oben ein Schriftfeld, links die Stückliste mit dem Ablagebereich für neue Zeichnungen, in der Mitte die Zeichnung mit Rasterfeldern, daneben das 3D-Modell. Die Prüfliste ist als Änderungstabelle gestaltet; jede Annahme erscheint darin als orange markierte Zeile. Der Auftritt nutzt ein Rot als Akzent, Weiß und Petrol; ein Logo wird nicht verwendet.

Leitprinzip: **Eine Kontur kennzeichnet einen unbestätigten Zustand (angenommen, nicht exportiert, abweichend, gesperrt); eine Füllung kennzeichnet einen gelesenen und geprüften Zustand.** Der Teilname bleibt als Kontur stehen, solange `all_ok` nicht `true` ist, und wird danach gefüllt dargestellt. Angenommene Maße erscheinen als orange Hilfsmaße in Klammern, gelesene Maße als gewöhnliche Maße.

Hell auf Weiß statt dunkel: Die Zeichnung (`drawing.png`) ist schwarz auf weiß, ein dunkles Blatt würde die Brücke von der Zeichnung zum Modell zerstören. Nur das 3D-Fenster hat einen dunklen Petrol-Verlauf.

## 1. Farben

Als Variablen in `style.css`. Die Kontrastwerte sind nach WCAG 2.x gerechnet: Text mindestens 4,5:1, Grafik und Bedienelemente mindestens 3:1.

| Variable | Wert | Verwendung |
| --- | --- | --- |
| `--paper` | `#ffffff` | Blatt |
| `--band` | `#f2f5f8` | Stückliste, Reiterleiste |
| `--ink` | `#222222` | Text |
| `--ink-2` | `#55606b` | Beschriftungen, Nebentext, Rahmen von Feldern |
| `--frame` | `#272727` | Rahmen- und Schriftfeldlinien, 1,5 px |
| `--hair` | `#c9d2db` | nur Zeilentrenner, nie Grenze eines Bedienelements |
| `--accent` | `#db0027` | Akzent, siehe Regel 1 |
| `--petrol` | `#046c83` | sekundäre Schaltflächen, Verknüpfungen, Fokus auf hellen Flächen |
| `--view-top`, `--view-bottom` | `#004959`, `#00404f` | Verlauf des 3D-Fensters |
| `--cyan` | `#00ebfd` | nur das Punktraster im 3D-Fenster |
| `--assume` | `#ff8000` | Annahme als Fläche, entspricht der Farbe im STEP und GLB; nie als Text auf Weiß |
| `--assume-ink`, `--assume-line`, `--assume-tint` | `#a64b00`, `#cc6300`, `#fff1e5` | oranger Text, orange Linien, Zeilenhintergrund einer Annahme |
| `--ok`, `--ok-ink` | `#3fae58`, `#1e7a34` | bestanden als Fläche und als Text |

Regeln:

1. **Das Akzentrot kodiert nie einen Zustand.** Es steht nur im Wortteil „2CAD“ der Wortmarke, im 145-px-Strich unter dem Teilnamen, in der einen primären Schaltfläche „New run“ und als 3-px-Balken der aktiven Zeile in der Stückliste.
2. **Orange heißt immer „angenommen statt gelesen“**, in der Oberfläche wie im Modell.
3. **Ein Fehler ist nicht rot**, sondern ein Kasten mit 45°-Schnittschraffur als Rand. Ein Wort wie „STOPPED“ steht auf einem weißen Feld in der Mitte; Fehlerkästen ohne Wort tragen nur den Text. Unter `forced-colors: active` ersetzt ein 2-px-Rahmen die Schraffur.
4. Kein Zustand hängt nur an der Farbe: jeder hat zusätzlich eine Form (Kontur, Fläche, Schraffur) und ein Wort.

Fokus: `outline: 2px solid var(--petrol)` auf hellen Flächen, auf dunklen Flächen (3D-Fenster, aktiver Reiter) Weiß.

## 2. Schrift

Inter Tight für Text, JetBrains Mono für IDs, Pfade, Operationen, Protokoll und Dateinamen; beide unter der SIL Open Font License, als woff2 in `d2c/web/static/vendor/fonts/` mit ihren Lizenztexten. Zur Laufzeit lädt nichts aus dem Netz; fehlen die Dateien, greifen Ersatzschriften (`font-display: swap`).

- Teilname im Schriftfeld: groß, 800, Versalien; als Kontur (`-webkit-text-stroke`), solange `all_ok` nicht `true` ist.
- Beschriftungen und Reiter: 12 px, 700, Versalien. Maßtexte: Inter Tight kursiv 500 mit Tabellenziffern (nach ISO 3098 B).
- Symbole (△, ✓, ✕, Pfeile) sind Inline-SVGs mit `aria-hidden`; das Wort daneben trägt die Bedeutung.

## 3. Layout

Bei 1440 × 900 ist alles ohne Scrollen der Seite sichtbar. Es scrollen nur die Teileliste und die Reiterfläche.

```
+--------------+-----------------------------+-----------+-----------+-----------+
| DRAWING2CAD  | TITLE                       | DRAWING   | SCALE     | TOLERANCE | STATUS
| [New run]    | Teilname (Kontur / gefüllt) | Datei     | 1:1  △1   | ISO 2768  | Annahmen, Prüfungen
+--------------+-----------------------------+-----------+-----------+-----------+
| PARTS        |  Zeichnung mit Rasterleiste |  3D-Fenster (Petrol-Verlauf)      |
| Stückliste   |  A-F / 1-8, Zoom             |  model-viewer, Ansichten, STEP-Link
| Ablagebereich+-----------------------------+-----------------------------------+
|              | Reiter: Review list, Checks, Tolerances, Traceability, Reference, Files       |
|              | Tabelle (scrollt, höchstens 30 vh)                                            |
+--------------+-------------------------------------------------------------------------------+
```

- Spalten: Stückliste 264 px (ab 1920 px Breite 300 px), Zeichnung und 3D-Fenster teilen den Rest.
- Die Rasterleiste A-F (Zeilen) und 1-8 (Spalten) steht am Zeichnungspanel; bei A4-Zeichnungen 1-4. Sie hat eine Bedeutung: Quellen in der Prüfliste nennen Ansicht und Rasterfeld.
- Unter 1024 px stehen Zeichnung und 3D-Fenster untereinander. Unter 768 px klappt das Schriftfeld auf drei Spalten um, Tabellenzeilen werden zu Blöcken. Die Seite scrollt nie horizontal.
- Linien: Schriftfeld und Panelgrenzen 1,5 px `--frame`, Tabellen nur mit Zeilentrennern, Eckenradius 0 (Schaltflächen 3 px).

## 4. Komponenten

**Drei Zustände eines Teils.** `export_step` schreibt Ergebnisdateien und `review.json` nur bei Erfolg; ein Fehlschlag lässt das letzte gute Ergebnis stehen:

- `all_ok === true`: exportiert und geprüft. Titel gefüllt, Häkchen.
- `all_ok === false`: exportiert, aber eine Prüfung nicht bestanden (etwa ein angenommenes Maß ohne PMI). Titel als Kontur, Kennzeichnung „CHECK FAILED“, im Schriftfeld „m/k OK“ mit Kreuz.
- `all_ok === null`: nicht exportiert (`spec.json` und `ops.json` vorhanden, noch nie ein erfolgreicher Export, kein STEP). Titel als Kontur, Kennzeichnung „NOT EXPORTED“, keine Prüfergebnisse.
- Ein fehlgeschlagener Export ist nur während des laufenden Vorgangs sichtbar (`tool_result` mit `ok: false`, `done` mit `reason`); nach dem Neuladen zeigt die Oberfläche wieder das letzte gute Ergebnis, oder „NOT EXPORTED“, falls es keines gibt.

Bausteine:

- **Schriftfeld:** Zellen „TITLE“, „DRAWING“ (Datei, „Sheet N“), „SCALE“ (`spec.scale`), „TOLERANCE“ (`general_tolerance`, ohne Angabe „not specified“), „STATUS“ („n ASSUMED“ und „m/k OK“ mit Häkchen, bei einer nicht bestandenen Prüfung mit Kreuz; ohne Export „NOT EXPORTED“, während eines Laufs „RUNNING“, danach „DONE“ oder „STOPPED“). „New run“ setzt nur den Fokus auf die Dateiauswahl im Ablagebereich. Enthält die Prüfliste einen Widerspruch, dessen Text einen Maßstab nennt, steht neben dem Maßstab ein Dreieck mit der Nummer des Eintrags (Link auf die Zeile); der gedruckte zweite Maßstab erscheint durchgestrichen (Titel „Scale printed on the drawing“).
- **Stückliste:** Positionsnummer, Name, Zahl der Annahmen (`review_count`, Dreieck in `--assume-ink`), Status (Häkchen, „CHECK FAILED“ oder „NOT EXPORTED“), Kennzeichnung „SCALE“, wenn `scale_conflict` in `/api/parts` `true` ist (die Prüfliste enthält einen Widerspruch zum Maßstab). `<ul role="listbox">` mit Pfeiltasten und Enter.
- **Zeichnung:** `<img>` mit Textalternative auf Weiß in der Rasterleiste. Zoom mit den Symbolschaltflächen „Zoom in“, „Zoom out“ und „Reset zoom“, den Tasten +, − und 0 und dem Mausrad. Bewegt man den Zeiger über eine Zeile mit Quelle oder fokussiert sie, bekommt das Rasterfeld einen Rahmen und das 3D-Fenster dreht in die zugehörige Ansicht.
- **3D-Fenster:** `<model-viewer camera-controls>` auf Petrol-Verlauf mit Cyan-Punktraster als Boden. Ansichten „FRONT“, „TOP“, „SIDE“ und „ISO“ (im Quelltext „Front“, „Top“, „Side“, „Iso“); beim Wechsel des Teils startet die Kamera vorn und dreht auf ISO. Gewinde sind grün. Ohne GLB steht dort „The 3D model appears after the export, with colours.“, beim Laden „Loading 3D model …“. Der Link „Download STEP“ lädt die STEP-Datei.
- **Reiter:** `role="tablist"` mit Pfeiltasten. Reihenfolge: „Run“ (nur während eines Laufs), „Runs“ (nur bei gespeicherten Protokollen), „Review list“, „Checks“, „Tolerances“, „Traceability“, „Reference“, „Files“. Die sechs letzten Reiter stehen immer da.
- **Prüfliste** (Reiter „Review list“): Spalten „No.“, „ID“, „Dimension“ (gedruckter Text; ein leerer Text heißt unbemaßt, dann der angenommene Wert als oranges Hilfsmaß in Klammern), „Finding“ („missing“, „ambiguous“, „low confidence“, „conflict“, „convention“), „Assumption“, „Reason“, „Source“ (`<Ansicht>/<Feld>`), „Faces“ (Zahl der markierten Flächen im STEP). Jede Zeile hat `--assume-tint`, eine orange Kante und `tabindex="0"`. Ohne Annahmen steht dort „No assumptions: every value comes from the drawing.“ Zeichnungshinweise stehen darunter unter „Drawing notes“.
- **Prüfungen** (Reiter „Checks“): Kennzahl „n/m“ mit der Beschriftung „checks passed“, darunter die Spalten „Check“, „Expected“, „Actual“ und „Result“ (Häkchen oder „FAILED“). Ohne Prüfergebnisse (nie exportiert oder der letzte Export bestand nicht): „No checks yet. The last export did not pass, or the part was never exported.“
- **Toleranzen** (Reiter „Tolerances“): Kennzahlen „n/m“ mit den Beschriftungen „nominal within tolerance“ und „tolerance midpoint within tolerance“, darunter je toleriertem Maß die Spalten „No.“, „ID“, „Dimension“ (gedruckter Text), „Nominal“, „Deviations“ (symmetrisch als ±, sonst oberes und unteres Abmaß), „Midpoint“, „Source“ („on dimension“, „fit“, „general tolerance“) und die beiden Urteile „Nominal result“ und „Midpoint result“ (Häkchen, „VIOLATED“, „not checked“; ein Urteil aus dem gemessenen Körper trägt den Titel „from the measured body“, „not checked“ heißt, kein Wert ist an das Maß gebunden). Zeilen mit Quelle im Feld `source` leuchten wie die Prüfliste die Rasterfelder der Zeichnung an; eine Zeile unter der Tabelle nennt, wie viele der tolerierten Maße geprüft sind („n of m toleranced dimensions are checked; …“). Darunter stehen, wenn die Zeichnung Rahmen nennt, unter „Geometric tolerances“ die Form- und Lagetoleranzen mit den Spalten „ID“, „Type“, „Value“, „Datums“, „Frame“ und „PMI in STEP“ („yes“ oder „no“). Gab es einen Export auf der Toleranzmitte, steht unter der Tabelle „Model at the tolerance midpoint:“ mit dem Link auf `<teil>_mid.step`. Ohne `review.json` steht dort „No tolerance table yet. It is created with the export.“, ohne tolerierte Maße „No toleranced dimensions: no dimension carries limit deviations, a fit or a general tolerance.“
- **Rückverfolgung** (Reiter „Traceability“): je gebundenem Wert Operation, Parameter, Wert, Maß-ID und gedruckter Text (bei einem angenommenen Wert „(assumed)“), z. B. `Op 2 add_holes · d = 8 ← d1 "Ø8"`; darunter „Values without a dimension“ mit gestrichelter Kante („None.“, wenn es keine gibt). Ohne `review.json` steht dort „No traceability yet. It is created with the export.“
- **Referenz** (Reiter „Reference“): Daten aus `results/EVAL.json`. Status „MATCH“, „DEVIATION“ oder „Reference invalid“, „volume deviation“, „largest bounding box deviation“, „Bounding box, sorted (own / reference)“ für die sortierten Hüllquader beider Modelle und ein Lineal mit einem Teilstrich je Teil und der Kennzahl „n/m“ unter „Matches“. Gezählt wird nur aus den vorhandenen Zeilen. Ohne Zeile: „No reference for this part.“
- **Dateien** (Reiter „Files“): eine Zeile je Datei (STEP, MID STEP, GLB, MD, JSON) mit „Download“, darunter „earlier versions: n“. Fehlt das STEP, steht dort ein Kasten „STOPPED“ mit dem Text „No STEP file: the export only runs if every check passes.“
- **Ablagebereich:** Datei per Ziehen oder über die Schaltfläche „Choose file“ mit dem Dateinamen daneben („No file chosen“; die native Dateiauswahl ist nur für Screenreader und Tastatur da, weil sie die Sprache des Browsers trägt) (PDF, PNG, JPG bis 20 MB), Teilname (Buchstaben, Ziffern, `_`, `-`; Umlaute auch zerlegt, der Server normalisiert auf NFC), Modell (aus `/api/models`) und die Schaltfläche „Create STEP“ (nach einem Start „Restart“). Die Felder heißen „Part name“ und „Model“ (Vorgabe „Agent default“), dazu die Optionen „Upload only“ und „Update existing model (new revision)“; der Hinweis im Ablagebereich lautet „Drop drawing (PDF, PNG, JPG) here“ und „Up to 20 MB“. Fehler stehen unter dem Feld (`role="alert"`); bei 400 und 413 die Meldung des Servers.

## 5. Orangefaden: Fläche und Prüfeintrag in beide Richtungen

Aktiv, wenn `files` das GLB und `marks.json` enthält und `marks.json` nicht leer ist.

- *3D → Liste:* Klick ins Modell (`materialFromPoint`) auf eine orange Fläche öffnet die Prüfliste; die Zeilen der IDs aus `marks.json` scrollen ins Bild und blitzen einmal auf. An der Klickstelle steht ein Hotspot mit den IDs.
- *Liste → 3D:* Zeiger oder Tastaturfokus auf einer Prüfzeile lässt das orange Material leuchten (`setEmissiveFactor`), die übrigen Materialien werden gedimmt.
- Das orange Material wird über die Farbe in `marks.json` gefunden (Toleranz ±2 je Kanal nach Umrechnung von linear nach sRGB).
- Grenze: Alle orangen Flächen teilen ein Material. Ein Klick unterscheidet die Prüfeinträge nicht, er markiert alle IDs aus `marks.json`.

## 6. Lauf: Kettenmaß und Protokoll

- Über dem Protokoll steht eine Kette mit fünf Abschnitten: READ, DESCRIBE, BUILD, CHECK, EXPORT (interne Kennungen `read`, `describe`, `build`, `check`, `export`). Jedes `tool_call` öffnet im Abschnitt seiner Stufe ein Maßsegment, `tool_result` schließt es mit Pfeil und Dauer. Die Ereignisse tragen keinen Zeitstempel; das Frontend misst selbst.
- Zuordnung: `render_drawing`, `read_drawing_text`, `measure_drawing` → READ; `submit_spec`, `check_spec` → DESCRIBE; `extrude`, `revolve`, `add_holes`, `edge_finish`, `run_script`, `edit_op`, `delete_op`, `move_op` → BUILD; `export_step` → EXPORT, dabei leuchtet CHECK. `get_state` und unbekannte Werkzeuge gehören zur zuletzt aktiven Stufe.
- Ein `tool_result` mit `ok: false` mitten im Bauen ist normal; das Segment bekommt einen schraffierten Rand, die Stufe erst, wenn der Lauf mit `done.ok === false` endet.
- Darunter das Textprotokoll (`role="log"`, `aria-live="polite"`): Zeit seit Start, Werkzeug, Kurzfassung; Argumente und Ergebnis klappen auf.
- Bei `done` schließt die Oberfläche den Event-Strom sofort, sonst würde `EventSource` neu verbinden und alle Ereignisse wiederholen. Das Ergebnis erscheint groß in der Kette („EXPORTED“ oder „STOPPED“ mit Grund); danach lädt die Stückliste einmal neu und öffnet das Teil. Bricht der Strom vor `done` ab, steht „Connection lost at mm:ss“.
- **Gespeicherte Läufe:** Der Reiter „Runs“ listet die Protokolle aus `results/<teil>/runs/` (Spalten „Started“, „Model“, „Result“, „Events“; ein laufender Lauf zeigt „running“, ohne Abschlusszeile „aborted“). Ein Klick spielt den Lauf über dieselbe Ansicht mit den aufgezeichneten Zeitabständen ab.

## 7. Bewegung

- Zeiten: 480 ms (Linien zeichnen, Schriftfeld), 1200 ms (Puls). Animiert werden nur `transform`, `opacity` und `clip-path`; keine Endlosschleifen, kein Auto-Rotate.
- `prefers-reduced-motion: reduce`: Linien erscheinen fertig, Segmente in Endlänge, die Kamera springt, statt des Leuchtens bleibt die Fläche orange und der Rest gedimmt, das Scrollen ist nicht animiert.

## 8. Zustände und Texte

- Leer: „No parts yet. Drop a drawing at the bottom left; the analysis starts automatically.“ (in der Stückliste „No parts yet.“)
- API nicht erreichbar: Schraffur-Kasten „The parts could not be loaded.“ mit „Try again“; der Rest bleibt bedienbar.
- Keine Emojis, keine Gedankenstriche in Oberflächentexten, keine festen Zahlen: Kennzahlen kommen aus den Ergebnissen.

## 9. Bewusst nicht

- Kein Logo, kein Glas, kein Glow, keine Schatten, keine Pillen. Der einzige Verlauf ist das 3D-Fenster.
- Kein Cyan außerhalb des 3D-Fensters, kein Akzentrot auf Daten, kein Dunkelmodus-Schalter.
- Keine Icon-Bibliothek, kein Framework, kein zusätzlicher Erstellungsschritt, kein CDN zur Laufzeit; einzige JS-Bibliothek ist `model-viewer`.

## 10. Sichtprüfung

- 1440 × 900 und 1920 × 1080 in Chrome, dazu 768 px Breite.
- Nur mit Tastatur: Stückliste, Reiter, Prüfzeile (löst das Leuchten aus), Dateiauswahl, Download. Fokus überall sichtbar.
- `prefers-reduced-motion` und `forced-colors` in den DevTools einschalten.
- Ein Lauf mit Test-Agent: keine doppelten Protokollzeilen, genau ein Neuladen der Stückliste nach `done`.
- Konsole ohne Fehler, Netzwerk ohne externe Anfragen.
