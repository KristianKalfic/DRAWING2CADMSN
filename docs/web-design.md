# Web-Oberfläche: Gestaltung „Prüfblatt“

Gestaltung der Web-Oberfläche (`d2c/web/static/`). Daten und Endpunkte: `docs/spec.md`, Abschnitt 12. Stand: Entwurf, `static/app.js` ist noch nicht gebaut. Das Feld `issues` in `/api/parts` und die Seitenwahl in `drawing.png` sind geplant.

## Idee

Die Oberfläche ist ein **geprüftes Zeichenblatt**: oben ein Schriftfeld, links die Stückliste, in der Mitte die Zeichnung mit Rasterfeldern. Die Review-Liste ist eine Änderungstabelle, und jede Annahme ist eine orange Prüfer-Korrektur. Aus dem weißen Blatt öffnet sich ein dunkles Petrol-Fenster mit dem 3D-Modell. Die Farben stammen aus der BCT-Unternehmensmarke, das Event-Cyan des Hackathons erscheint nur im 3D-Fenster.

Eine Regel trägt das ganze Design: **Outline heißt nicht bestätigt (angenommen, nicht exportiert, abweichend, blockiert), gefüllt heißt gelesen und geprüft.** Der Teilename steht als Outline da, solange `all_ok` nicht `true` ist, danach gefüllt. Angenommene Maße sind orange Hilfsmaße in Klammern, gelesene Maße normale Maße.

Warum diese Richtung:

- Die Jury besteht aus CAD- und PLM-Ingenieuren. Ein Schriftfeld nach ISO 7200, Hilfsmaße nach ISO 129-1 und Schnittschraffur erkennen sie sofort. Ein generisches Dashboard sieht für sie aus wie jedes andere.
- BCT verkauft selbst „BCT 3D-Raster“, ein Produkt, das Rasterzeichnungen in NX überführt. Dessen Produktseite zeigt eine CAD-Zeichnung unter einem dunklen Petrol-Verlauf. Das 3D-Fenster zitiert genau dieses Motiv.
- Helles Papier funktioniert auf jedem Beamer. Dunkle Flächen verlieren dort ihre Abstufungen.
- `drawing.png` ist schwarz auf weiß. Ein dunkles Blatt würde die Brücke Zeichnung → Modell zerstören.

## BCT-Marke (Quelle: bct-technology.com und BCTs Hackathon-Folien, geprüft)

- Primärfarbe BCT-Rot `#DB0027`, Hover `#9D0521`. Weiß dominiert, Hellgrau-Bänder `#F2F5F8`, Anthrazit `#272727`, Text `#222`.
- Sekundär Petrol: Button `#046C83`, Verlauf `#14A8C5` bis `#004959`.
- Schrift Inter Tight. Überschriften 700 mit enger Laufweite, Buttons mit 3 px Radius und `letter-spacing: 1px`.
- Markenzeichen: In einer dunklen Überschrift ist **ein Wortteil rot** („Gemeinsam **MEHR**wert schaffen“). Dazu ein kurzer roter Strich (145 px) als Trenner.
- Das Cyan/Schwarz der Folie (`#00EBFD`, „BLACK FOREST“ als Outline-Versalien) ist Event-Branding, nicht BCT.
- **Das BCT-Logo wird nicht verwendet** (keine Freigabe).

## 1. Farben

In `style.css` als Variablen. Die Kontrastwerte sind nach WCAG 2.x gerechnet und nachgeprüft. Text braucht mindestens 4,5:1, Grafik und Bedienelemente mindestens 3:1.

```css
:root {
  --paper: #ffffff;       /* Blatt */
  --band: #f2f5f8;        /* Stückliste, Reiterleiste */
  --ink: #222222;         /* Text: 15,9 auf paper, 14,5 auf band */
  --ink-2: #55606b;       /* Labels, Nebentext, Rahmen von Feldern und Dropzone: 6,4 auf paper, 5,9 auf band */
  --frame: #272727;       /* Rahmen- und Schriftfeldlinien, 1,5 px */
  --hair: #c9d2db;        /* nur Zeilentrenner und Deko, nie Grenze eines Bedienelements (1,5) */
  --bct: #db0027;         /* Marke: Weiß darauf 5,2 */
  --bct-press: #9d0521;   /* Hover/Active des Primärbuttons: 8,5 */
  --petrol: #046c83;      /* Sekundärbuttons, Links, Fokus auf hellen Flächen, Zellen-Highlight: 6,0 auf paper */
  --view-top: #004959;    /* 3D-Fenster, Verlauf oben; Weiß darauf 10,0 */
  --view-bottom: #00404f; /* 3D-Fenster, Verlauf unten; Weiß darauf 11,4 */
  --cyan: #00ebfd;        /* Event-Zitat, NUR im 3D-Fenster: 6,8 auf view-top */
  --assume: #ff8000;      /* Annahme = Farbe im STEP/GLB. Nur als Fläche oder im 3D-Fenster, nie Text auf Weiß (2,5) */
  --assume-ink: #a64b00;  /* oranger Text auf Weiß: 5,8 */
  --assume-line: #cc6300; /* orange Linien auf Weiß: 3,9 */
  --assume-tint: #fff1e5; /* Zeilenhintergrund einer Annahme; assume-ink darauf 5,2 */
  --ok: #3fae58;          /* ok und Gewinde als Fläche; ink darauf 5,6 */
  --ok-ink: #1e7a34;      /* ok als Text: 5,4 */
}
```

Regeln für die drei warmen Farben (die wichtigsten Regeln der Datei):

1. **BCT-Rot kodiert nie einen Zustand.** Es steht nur an drei Stellen: im Wortteil „2CAD“ der Wortmarke, im 145-px-Strich unter dem Teilenamen und im **einen** Primärbutton „New run“ im Schriftfeld. Dazu der 3-px-Balken der aktiven Zeile in der Stückliste (Navigation, keine Daten).
2. **Orange heißt immer „angenommen statt gelesen“**, in der Oberfläche wie im Modell.
3. **Ein Fehler ist nicht rot**, sondern ein Kasten mit 45°-Schnittschraffur als Rand (`repeating-linear-gradient(45deg, var(--ink) 0 1.5px, transparent 1.5px 7px)`). Das Wort „FAIL“ oder „BLOCKED“ steht auf einem weißen Feld in der Mitte, nie direkt auf der Schraffur. Unter `@media (forced-colors: active)` ersetzt ein 2-px-Rahmen die Schraffur.
4. Kein Zustand hängt nur an der Farbe. Jeder hat zusätzlich eine Form (Outline, Fläche, Schraffur) und ein Wort.

Fokus: `outline: 2px solid var(--petrol); outline-offset: 2px` auf hellen Flächen. Im 3D-Fenster und auf `--frame`-Flächen (aktiver Reiter) Weiß, denn Petrol erreicht dort nur 1,7 und 2,5.

## 2. Schrift

Beide Schriften stehen unter der OFL. Fertige woff2-Dateien mit Latin-Subset gibt es bei Fontsource. Einmalig beim Bauen holen (alle liefern 200) und unter `d2c/web/static/vendor/fonts/` ablegen. Zur Laufzeit lädt nichts aus dem Netz.

```
cd d2c/web/static/vendor/fonts
curl -L -o InterTight-wght.woff2        "https://cdn.jsdelivr.net/fontsource/fonts/inter-tight:vf@latest/latin-wght-normal.woff2"
curl -L -o InterTight-wght-italic.woff2 "https://cdn.jsdelivr.net/fontsource/fonts/inter-tight:vf@latest/latin-wght-italic.woff2"
curl -L -o JetBrainsMono-wght.woff2     "https://cdn.jsdelivr.net/fontsource/fonts/jetbrains-mono:vf@latest/latin-wght-normal.woff2"
curl -L -o OFL-InterTight.txt    https://raw.githubusercontent.com/google/fonts/main/ofl/intertight/OFL.txt
curl -L -o OFL-JetBrainsMono.txt https://raw.githubusercontent.com/google/fonts/main/ofl/jetbrainsmono/OFL.txt
```

- Inter Tight (BCT-Hausschrift): Stack `"Inter Tight", "Helvetica Neue", Arial, sans-serif`. `@font-face` mit `font-weight: 100 900`.
- JetBrains Mono für IDs, Pfade, Ops, Log und Dateinamen: Stack `"JetBrains Mono", ui-monospace, "SF Mono", Menlo, monospace`.
- `font-display: swap`. Fehlen die Dateien, sieht die Seite mit den Fallbacks immer noch ordentlich aus.
- **Symbole △, ✓, ✕ und Pfeile sind nicht im Latin-Subset.** Sie kommen als kleine Inline-SVGs (`aria-hidden="true"`, das Wort daneben trägt die Bedeutung). Ø, ×, ± und ° sind im Subset.

Skala (px, Zeilenhöhe, Gewicht):

- Teilename im Schriftfeld: 48, 1.05, 800, Versalien, `letter-spacing: -0.01em`. Outline über `-webkit-text-stroke: 1.5px var(--ink); color: transparent`, gefüllt bei `all_ok === true`. Outline nur ab 32 px, darunter wird sie unsauber; kleinere Zustände sind Tags.
- Kennzahl (Benchmark, Validation-Kopf): 56, 1, 700, `tabular-nums`.
- H2: 28, 1.15, 600. Schriftfeld-Wert: 18, 1.2, 600.
- Body: 15, 1.45, 400, höchstens 70ch.
- Label und Reiter: 12, 1.2, 700, Versalien, `letter-spacing: 1px`.
- **Maßtext**: Inter Tight *Italic* 500, 14 px, `tabular-nums`. Die Kursive zitiert die Normschrift nach ISO 3098 B. Gilt für jeden Maßwert im Blatt.
- Mono: 13, 1.5.

## 3. Layout

Bei 1440 × 900 ist alles ohne Scrollen der Seite sichtbar. Es scrollen nur die Teileliste in der Stückliste und die Reiterfläche, die Dropzone bleibt unten fest. Zeilen des Hauptbereichs: `grid-template-rows: 1fr 44px minmax(150px, 30vh)`, die Bühne bekommt den Rest. Eine feste Bühnenhöhe lässt bei Teilen mit wenigen Review-Einträgen eine große leere Fläche (im Mockup geprüft).

```
+--------------+-----------------------------+-----------+-----------+-----------+-------------+
| DRAWING2CAD  | TITLE                       | DRAWING   | SCALE     | TOLERANCE | STATUS      |  88 px
| ("2CAD" rot) | PLATE 80X50 (48, Outline)   | plate.pdf | 1:1  △1   | ISO 2768-m| 1 ASSUMED   |  Schriftfeld
| [ New run ]  | ===== (145 px BCT-Rot)      | page 1    |           |           | 14/14 OK    |
+--------------+---1-----2-----3-----4-----5-----6---+-----------------------------------------+
| PARTS 21     A  drawing.png auf Weiss              A |  3D-FENSTER  Petrol-Verlauf             |
| Stueckliste  |                         +---+       |  Cyan-Punktraster als Boden             |  Rest
| 01 Bracket△2 B                         |C4 |      B |  model-viewer, orange Flaechen          |
| 02 Shaft  ✓  |                         +---+       |  [FRONT] [TOP] [SIDE] [ISO]   rv1 Tag   |
| 03 Plate  △1 C                                    C |                                         |
| (scrollt)    +---1-----2-----3-----4-----5-----6---+-----------------------------------------+
| 264 px       | REVIEW 1 . VALIDATION 14/14 . TRACEABILITY . BENCHMARK . FILES          44 px  |
| Band-Grau    | △  ID   DIMENSION  ISSUE      ASSUMPTION       SOURCE   FACES                   |
| ------------ | 1  rv1  (10)       missing    thickness 10 mm  front C4 1                       |  max. 30vh, scrollt
| Dropzone fix |                                                                                |
+--------------+--------------------------------------------------------------------------------+
```

- Spalten: Stückliste 264 px, Zeichnung und 3D-Fenster teilen den Rest 1 : 1.
- Rasterleiste A-F (Zeilen) und 1-8 (Spalten) nur am Zeichnungspanel, 16 px breit, Ziffern in `--ink-2`. Bei A4-Zeichnungen 1-4. Sie hat eine Bedeutung (S1), keine Deko.
- 1920 × 1080: Stückliste 300 px, Zeichnung zu 3D 1 : 1,2.
- Unter 1024 px: Zeichnung und 3D untereinander (3D zuerst), Stückliste wird eine horizontale Scroll-Leiste über der Bühne.
- Unter 768 px: Schriftfeld klappt auf 2 × 3 Zellen, Reiter scrollen horizontal, Tabellenzeilen werden Blöcke mit Label über Wert. Kein Demo-Ziel, aber bedienbar, kein horizontales Scrollen der Seite.
- Linien: Schriftfeld und Panelgrenzen 1,5 px `--frame`. Tabellen nur mit Zeilentrennern in `--hair`, keine Kästen um jede Zelle. Eckenradius 0, nur Buttons 3 px wie bei BCT.

## 4. Komponenten

**Drei Zustände eines Teils.** `Session.export` schreibt `review.json` nur bei Erfolg und löscht es bei einem Fehlschlag. Im Ergebnisordner gibt es deshalb nie ein Teil mit fehlgeschlagenen Prüfungen, sondern:

- `all_ok === true`: exportiert und geprüft. Titel gefüllt, ✓.
- `all_ok === null`: nicht exportiert (Spec und Ops da, kein STEP). Titel Outline, Tag „NOT EXPORTED“ (Outline-Rahmen), keine Validation-Zahlen.
- Ein fehlgeschlagener Export ist nur im **Live-Lauf** sichtbar (S3, `tool_result` mit `ok: false` und `done` mit `reason`). Nach dem Neuladen ist das Teil „not exported“.

Komponenten:

- **Schriftfeld (Kopf):** echtes Zellraster mit den Zellen TITLE, DRAWING (Dateiname, „page N“ aus `spec.page`), SCALE (`spec.scale`), TOLERANCE (`general_tolerance`, sonst „not stated“), STATUS (Zahl der Annahmen und „14/14 OK“, bei „not exported“ nur das Tag). Labels 12 px Versalien in `--ink-2`, Werte 18/600. Links die Wortmarke „Drawing**2CAD**“ in Inter Tight 800 („2CAD“ in BCT-Rot) und darunter der Primärbutton „New run“ (Weiß auf BCT-Rot, Hover `--bct-press`, gedrückt `translateY(1px)`). Er startet nichts, sondern setzt den Fokus in die Dropzone.
- **Stückliste:** Positionsnummer in Mono, Name, rechts △ mit Zahl der Annahmen (`review_count`) in `--assume-ink` (fehlt bei 0), dazu ein Mono-Tag „SCALE“, wenn `issues` (Feld in `/api/parts`) `"conflict"` enthält, und der Status ✓ oder „NOT EXPORTED“. Aktive Zeile mit 3-px-Balken in BCT-Rot links. Muster: `<ul role="listbox">` mit `role="option"`, `aria-selected`, Roving-Tabindex, ↑/↓ und Enter. Kopf „PARTS 21“.
- **Zeichnung:** `<img alt="Drawing of <title>, page N">` auf Weiß in der Rasterleiste; der Review-Reiter ist die Textalternative zu den Maßen. Zoom (Kür): Buttons +, − und Reset unten rechts, Tasten +, − und 0 bei Fokus auf dem Bild, zusätzlich Mausrad am Zeiger.
- **3D-Fenster:** `<model-viewer camera-controls>` auf `linear-gradient(var(--view-top), var(--view-bottom))`, darunter ein Cyan-Punktraster als Boden (`radial-gradient(var(--cyan) 1px, transparent 1.2px)`, `background-size: 16px 16px`, 25 % Deckkraft, nur untere Hälfte über eine Maske). Unten links die Ansichtsknöpfe FRONT, TOP, SIDE, ISO als Ghost-Buttons (weiße Haarlinie, Weiß-Text, `aria-pressed`). Gewinde bleiben grün, so wie das GLB sie liefert. `alt` nennt Teilename und Zahl der Annahmen. **Ohne GLB** (bei „not exported“) zeigt das Fenster das Punktraster und den Satz „3D model appears after export with colors.“
- **Reiter:** `role="tablist"`, Buttons mit `role="tab"`, `aria-selected`, `aria-controls`, Roving-Tabindex und ←/→, Inhalt in `role="tabpanel"` mit `aria-labelledby`. Aktiver Reiter als `--frame`-Kasten mit weißem Text. Reihenfolge Review, Validation, Traceability, Benchmark, Files. Während eines Laufs steht vorne ein Reiter „Run“ (S3).
- **Review (Änderungstabelle):** Spalten △ (laufende Nummer), ID (Mono), DIMENSION (Texte aus `refs[].text`; ein leerer Text heißt unbemaßt, dann der angenommene Wert als oranges Hilfsmaß in Klammern), ISSUE (`missing`, `ambiguous`, `low_confidence`, `conflict`, `convention` als Mono-Tag), ASSUMPTION, REASON, SOURCE (`sources`, Strings `"<view-id>/<cell>"`, `-` für kein Raster), FACES (`marked_faces`, `null` als „-“). Jede Zeile `--assume-tint` mit 4-px-Kante `--assume-line` links und `tabindex="0"`, damit das Leuchten (S2) auch per Tastatur auslöst. `notes` darunter als graue Liste „Drawing notes“.
- **Validation:** oben die Kennzahl „14/14“ (Zahl ok von allen `validation`-Zeilen). Darunter Zeilen CHECK, EXPECTED, ACTUAL (Mono) und ✓. Bei „not exported“: „No validation yet. The last export did not pass, or the part was never exported.“
- **Traceability:** `ops.json` per `/files/<teil>/ops.json` nachladen, denn `/api/parts/{name}` liefert es nicht. Zeilen in Mono: `op 2 holes · d = 8  ←  d1 "Ø8"`, also `op_index + 1`, Opname aus `ops[op_index].op`, dann `path`, `value`, `dimension` und `text`. Darunter Block „Values without dimension reference“ (`unreferenced`) mit gestrichelter Kante in `--assume-line`, denn diese Werte sind nicht durch ein Maß belegt.
- **Benchmark:** S5. Ohne `eval`: „No reference for this part.“
- **Files:** eine Zeile pro Datei aus `files`: Typ-Kürzel groß (STEP, MID STEP, GLB, MD, JSON), Dateiname in Mono, Link mit `download`. Bei „not exported“ statt der STEP-Zeile ein Schraffur-Kasten „BLOCKED“ mit „No STEP file: export only runs when every check passes.“ (das Prinzip „kein Export bei Rot“ sichtbar gemacht).
- **Upload (Dropzone, unten fest in der Stückliste):** Rahmen 1,5 px gestrichelt in `--ink-2`, Text „Drop a drawing (PDF, PNG, JPG)“, bei `dragover` gefüllt `--band`. Darin eine sichtbare, fokussierbare `<input type="file" accept=".pdf,.png,.jpg,.jpeg">`; Ziehen ist nur ein zweiter Weg. Felder Part name (`pattern="[\w-]+"`) und Model (optional), Labels über den Feldern, Feldrahmen 1 px `--ink-2`. Submit „Start run“ als Petrol-Button. Fehler unter dem Feld mit `role="alert"` und `aria-describedby`: 400 und 413 mit der Server-Meldung. 503 als Hinweis mit Petrol-Kante: die Server-Meldung wörtlich plus „Recorded parts stay browsable.“

## 5. Signature-Momente

Jeder Moment hat eine Stufe: **T1** zuerst, dann **T2**, **Kür** zuletzt.

**S0 Gezeichnet, dann gefüllt (T1, 15 min).** Beim Öffnen eines Teils zeichnen sich die Linien des Schriftfelds per `clip-path` oder `scaleX` in 480 ms. Der Teilename erscheint als Outline und füllt sich, wenn `all_ok === true`. Bei „not exported“ bleibt er Outline mit Tag.

**S2 Orangefaden: Fläche und Review-Eintrag in beide Richtungen (T2, 45 min, das Kernstück der Demo). Braucht GLB und `marks.json`.** Bis dahin hinter einer Abfrage: nur aktiv, wenn `files` die GLB und `marks.json` enthält und `marks.json` nicht `{}` ist.

- *3D → Liste:* Klick ins Modell → `viewer.materialFromPoint(x, y)`. Ist es das orange Material, öffnet sich der Reiter Review, die Zeilen der IDs aus `marks.json` scrollen ins Bild und blitzen einmal auf. Klick auf leeren Hintergrund (`null`) tut nichts.
- *Liste → 3D:* Hover oder Fokus auf eine Review-Zeile → das orange Material leuchtet über `material.setEmissiveFactor([0.5, 0.12, 0])` (mit `[1, 0.4, 0]` wird es im Mockup blass-pfirsich), alle anderen Materialien werden über `pbrMetallicRoughness.setBaseColorFactor` auf 35 % gedimmt. Beim Verlassen die gemerkten Ausgangswerte zurücksetzen.
- Hotspot (Kür): An der Klickstelle setzt `viewer.positionAndNormalFromPoint(x, y)` einen Hotspot mit orangem Tag „rv1“ (mehrere IDs mit Komma). Es gibt immer nur einen: den alten Button vor dem neuen entfernen (`slot="hotspot-mark"`).
- Material finden: Für jedes Material in `viewer.model.materials` den `baseColorFactor` von linear nach sRGB wandeln und mit den Schlüsseln in `marks.json` vergleichen, **mit Toleranz ±2 je Kanal**, nicht über exakte Hex-Strings. Der Exporter speichert Orange als `[1, 0.214, 0]`, das ergibt rechnerisch genau 127,5 im grünen Kanal.

```js
const toSrgb = (c) => (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055) * 255;
const sameColor = (factor, hex) =>
  [1, 3, 5].every((i, k) => Math.abs(toSrgb(factor[k]) - parseInt(hex.slice(i, i + 2), 16)) <= 2);
```

- Geprüft mit dem vendorten model-viewer und einem echten GLB aus `write_glb`: Das GLB hat die Materialien `mat_0` (Körper) und `mat_1` (orange), dazu ein grünes bei Gewinden. `materialFromPoint` trifft `mat_1` auf den orangen Flächen, `setEmissiveFactor` lässt sie sichtbar aufleuchten.
- Grenze: Alle orangen Flächen teilen **ein** Material. Der Klick unterscheidet rv1 nicht von rv2, er markiert alle IDs aus `marks.json`. Das ist für die Demo in Ordnung. Genauer geht es mit einem eigenen Orange-Ton im Export je Review-ID (z. B. `#ff8000`, `#ff8001`); das Frontend braucht dafür keine Änderung.

**S3 Kettenmaß-Protokoll: Live-Lauf als Kettenbemaßung (T2, 50 min). Ohne Agent mit einem Fake-Agenten testen.**

- Über dem Log läuft eine horizontale Kettenbemaßung mit fünf Abschnitten: READ, DESCRIBE, BUILD, CHECK, EXPORT, getrennt durch Schnittkennzeichen (Inline-SVG-Dreieck plus Buchstabe A bis E).
- Jedes `tool_call` öffnet im Abschnitt seiner Stufe ein Maßsegment. Der Toolname steht kursiv als Maßtext darüber, das Segment wächst per `flex-grow` mit der Laufzeit. `tool_result` schließt es mit Maßpfeil (SVG-`marker`) und Dauer „1.8 s“. Die Ereignisse haben weder ID noch Zeitstempel: das Frontend misst die Zeit selbst (`performance.now()` beim Empfang) und paart `tool_call` und `tool_result` per Warteschlange je Toolname.
- Zuordnung Tool → Stufe in `app.js`:
  - READ: `render_drawing`, `read_drawing_text`, `measure_drawing`
  - DESCRIBE: `submit_spec`, `check_spec`
  - BUILD: `extrude`, `revolve`, `add_holes`, `edge_finish`, `run_script`, `edit_op`, `delete_op`, `move_op`
  - EXPORT: `export_step`
  - stufenneutral: `get_state` (der Agent ruft es mitten im Bauen auf), es erscheint nur im Log
  - unbekannte Tools gehen in die zuletzt aktive Stufe
  - CHECK leuchtet, sobald `export_step` aufgerufen wird (die Prüfungen laufen im Export), und bekommt Schraffur, wenn dessen Ergebnis `ok: false` ist
- Ein `tool_result` mit `ok: false` mitten im Bauen ist normal (das Modell korrigiert sich). Das Segment bekommt einen schraffierten Rand, die **Stufe** wird erst schraffiert, wenn der Lauf mit `done.ok === false` endet und dies die letzte aktive Stufe war.
- Darunter das Textlog als Änderungstabelle in Mono, `role="log" aria-live="polite"`: Zeit seit Start, Tool, kurze Zusammenfassung. Die Argumente (`args`) und das Ergebnis (`text`) stecken aufklappbar in `<details>`. Es scrollt mit, außer der Nutzer hat nach oben gescrollt.
- **`done` beendet den Stream: sofort `eventSource.close()`.** Der Server schließt die Verbindung nach `done`, und `EventSource` würde sich sonst neu verbinden und alle Ereignisse ab dem ersten noch einmal schicken (doppelte Zeilen, zweites `done`). `onerror` gilt nur als „Connection lost“, wenn noch kein `done` kam.
- `done` setzt das Ergebnis groß in die Kette: „EXPORTED“ gefüllt oder „BLOCKED“ als Outline im Schraffur-Kasten, dazu `reason`. Danach lädt die Stückliste einmal neu und öffnet das neue Teil.
- Ereignisse defensiv lesen: Felder nach `spec.md` 5.7, unbekannte Typen und fehlende Felder als einfache Mono-Zeile.

**S4 Prüfvermerk: Maßstabskonflikt (T2 das △, Kür das Durchstreichen, 10 + 15 min).**

- T2: Hat das offene Teil einen Review-Eintrag mit `issue == "conflict"`, dessen `assumption` oder `reason` „scale“ enthält, steht in der SCALE-Zelle neben `spec.scale` ein Änderungsdreieck △ mit der Nummer des Eintrags in `--assume-ink`. Es verlinkt auf die Review-Zeile. In der Stückliste markiert das Tag „SCALE“ alle Teile mit einem Konflikt (Feld `issues`), so sieht man die Teile mit falschem Maßstab auf einen Blick. Grenze: „SCALE“ in der Liste heißt „hat einen Konflikt“; ob es der Maßstab ist, zeigt erst das offene Teil.
- Kür: Findet das Frontend im Review-Text einen zweiten Maßstab der Form `\d+:\d+` (den gedruckten), steht er durchgestrichen mit einer Linie in `--assume-line` vor `spec.scale`. Sonst bleibt es beim △.

**S5 Passungsfeld: Benchmark (T1 Zahl, Kür Lineal, 15 + 25 min).**

- T1: Im Reiter Benchmark `match` als Wort (MATCH gefüllt in `--ok`, DIFF als Outline, „reference invalid“ grau gestrichelt), dazu `dv_pct` und `dbbox_max_mm` als Kennzahlen und die sortierte BBox eigen gegen Referenz als drei Mono-Zeilen.
- Kür: ein Lineal mit einem Teilstrich je Teil aus `/api/parts` und die Kennzahl „x/y MATCH“, nur aus den echten `eval`-Zeilen gezählt; Teile mit `reference invalid` zählen nicht mit und stehen daneben. Ein Klick auf einen Strich öffnet das Teil. **Keine Zahl fest eintragen.**

**S1 Bezugslinie: Maß → Rasterfeld → Ansicht (Kür, 40 min)..**

- Hover oder Fokus auf eine Review- oder Traceability-Zeile: Das Rasterfeld (z. B. C4) bekommt ein 2-px-Petrol-Rechteck auf der Zeichnung, die Kamera dreht in die Ansicht dieses Maßes.
- Woher Ansicht und Feld kommen:
  - Review-Zeile: `sources` ist eine Liste von Strings `"<view-id>/<cell>"`, per `split("/")` zerlegen; `cell` ist `-` ohne Raster. Refs auf Features oder Notes haben keine Source.
  - Traceability-Zeile: `spec.dimensions.find((d) => d.id === row.dimension).source` mit `view` und `cell` (beide können `null` sein).
  - Ansicht: `spec.views.find((v) => v.id === view).kind`. Mehrere Zellen: alle markieren, Kamera auf die erste.
- Ansichten in einem Objekt, das man am Demo-Teil kalibriert (CadQuery ist Z-oben, glTF Y-oben):
  `const VIEWS = { front: "0deg 90deg auto", top: "0deg 0deg auto", side: "90deg 90deg auto", iso: "45deg 60deg auto" };` mit `viewer.fieldOfView = "12deg"` für eine fast orthografische Ansicht. `section`, `detail` und unbekannte Arten bewegen die Kamera nicht. Die Ansichtsknöpfe nutzen dasselbe Objekt.
- Die Zelle wird aus dem Bildrahmen geteilt durch 6 × 8 berechnet (A4: 6 × 4). Der Rahmen der PNG ist nicht das Blatt, deshalb Ränder als Konstante in Prozent kalibrieren. Ohne Zelle nur die Kamera.
- Optional eine SVG-Bezugslinie in `--petrol` von der Zeile zur Zelle (kein Cyan, sie läuft über das Blatt). Das ist der erste Kandidat zum Streichen.

## 6. Bewegung

- Tokens: `--dur-press: 120ms`, `--dur-ui: 240ms`, `--dur-draw: 480ms` (BCT nutzt 0,4 bis 0,5 s), `--dur-pulse: 1200ms`.
- Kurven: `--ease-out: cubic-bezier(.2, .8, .2, 1)` für UI, `--ease-draw: cubic-bezier(.65, 0, .35, 1)` für Linien. model-viewer `interpolation-decay="140"` (eine Zeitkonstante, keine feste Dauer; die Fahrt dauert damit etwa eine halbe Sekunde).
- Animiert werden nur `transform`, `opacity` und `clip-path`. Keine Endlosschleifen, kein Auto-Rotate.
- Beim Wechsel des Teils startet die Kamera in der Vorderansicht und dreht auf ISO, so hebt sich das Modell aus der Zeichnung.
- `prefers-reduced-motion: reduce`: Linien erscheinen fertig, Segmente in Endlänge, die Kamera springt (`viewer.jumpCameraToGoal()`), statt des Leuchtens bleibt die Fläche orange und der Rest gedimmt, `scrollIntoView` mit `behavior: "auto"`.

## 7. Zustände und Texte (Englisch)

- Leer: Blatt mit leerem Schriftfeld und Rahmen, mittig „No parts yet. Run the demo or drop a drawing.“, Fokus springt zur Dropzone.
- Laden: Konstruktionslinien als Skelett, gestrichelte Schriftfeld-Zellen, statt des Bildes Hilfslinien. Kein Spinner.
- API nicht erreichbar: Schraffur-Kasten im betroffenen Panel, „Could not load parts.“ und „Retry“. Der Rest bleibt bedienbar.
- SSE bricht vor `done` ab: Logzeile „Connection lost at 00:41“, das offene Segment bekommt einen schraffierten Rand.
- Keine Emojis, keine Gedankenstriche in UI-Texten, keine erfundenen Zahlen.

## 8. Bewusst nicht

- Kein BCT-Logo, kein Glas, kein Glow, kein Verlaufstext, keine Schatten, keine Pillen. Der einzige Verlauf ist das 3D-Fenster (BCT-Motiv).
- Kein Cyan außerhalb des 3D-Fensters, kein BCT-Rot auf Daten, kein Dark-Mode-Schalter.
- Keine Icon-Bibliothek: Zeichnungssymbole als Inline-SVG.
- Kein Framework, kein Build-Schritt, kein CDN zur Laufzeit, keine weitere JS-Bibliothek außer `model-viewer`.

## 9. Priorität

- **T1:** Layout mit Schriftfeld, Stückliste, Zeichnung, 3D-Fenster, Reiter, Zustände, S0, S5 als Zahl.
- **T2:** Dropzone, S3, S2 ohne Hotspot, S4 als △.
- **Kür:** Hotspot, S4 Durchstreichen, S5 Lineal, S1, Zoom.

Nie streichen: S2 im Kern, die Kette in S3 mit `close()` bei `done`, die Orange-Semantik, die Exportsperre, Tastatur, Kontrast, reduzierte Bewegung.

## 10. Sichtabnahme

- 1440 × 900 und 1920 × 1080 im Chrome, dazu einmal mit 768 px Breite.
- Einmal am Beamer oder mit Helligkeit auf Maximum: Schriftfeldlinien, Hilfsmaße und Schraffur bleiben erkennbar.
- Nur mit Tastatur: Stückliste, Reiter, Review-Zeile (löst das Leuchten aus), Dateiauswahl der Dropzone, Download. Fokus überall sichtbar, auch im 3D-Fenster.
- `prefers-reduced-motion` und `forced-colors` in den DevTools einschalten und durchklicken.
- Ein Live-Lauf mit Fake-Agent: keine doppelten Logzeilen, genau ein Neuladen der Stückliste nach `done`.
- Konsole ohne Fehler, Netzwerk ohne externe Anfragen.
- Screenshot `docs/img/web-ui.png` mit dem Demo-Teil, Review-Zeile im Fokus, sodass die orange Fläche leuchtet.
