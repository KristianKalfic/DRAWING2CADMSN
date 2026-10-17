# Drawing2CAD: Teamplan

Stand: Sa 17.10.2026, 10:15, geprüft gegen den Kern-Code, den Challenge-Text und den Zeitplan. Abgabe **So 11:30**, Pitch **So ab 13:00**. Abgabe-Repo: **https://github.com/KristianKalfic/DRAWING2CADMSN** (öffentlich).

**Dieser Plan bleibt privat.** Er enthält Maße aus den BCT-Zeichnungen und unsere Strategie. Das Abgabe-Repo ist öffentlich und für jedes andere Team derselben Challenge lesbar. Deshalb kommt `docs/team/` nie ins Repo, auch nicht auf `dev` (Abschnitt 8, Entscheidung 1).

Drei Dateien reichen für den Einstieg:

1. Diese Datei: Strategie, Rollen, Paket-Board, Zeitplan, Regeln.
2. `spec.md`: Anforderungen, Daten-Fakten, Architektur und die Schnittstellen zwischen den Paketen. Das Architekturbild liegt daneben als Excalidraw-Datei `architektur.excalidraw` (Vorschau `architektur.png`, `architektur.svg`).
3. `pakete/`: ein Arbeitspaket pro Datei. Wer sein Paket und `spec.md` liest, kann ohne Rückfrage loslegen.

**So machst du mit:**

1. Sofort `docker pull python:3.12-slim` starten. Das spart später Zeit beim ersten Build.
2. Paket im Board (Abschnitt 4) wählen und im Team-Chat ansagen: „WP-xx: Name“. Das Board im Team-Chat ist maßgeblich, die Tabelle unten ist der Startstand.
3. Paketdatei lesen, einrichten (Abschnitt 7), loslegen. Ein Paket hat genau eine verantwortliche Person.

---

## 1. Was wir bauen

Eine KI liest eine technische 2D-Zeichnung und schreibt eine strukturierte Beschreibung des Teils (JSON). Über einen lokalen MCP-Server, der ohne Internet in Docker läuft, baut sie daraus Schritt für Schritt ein CadQuery-Modell. Nach jedem Schritt prüft der Server das Solid, alle Maße und den STEP-Re-Import. Am Ende steht ein STEP AP242 mit farbig markierten Annahmen und PMI, dazu eine Review-Liste. Ein Benchmark vergleicht unsere Modelle mit den BCT-Referenzen.

**Stand Sa 10:15:** Es gibt zwei Codestände.

- **Team-Repo (erster Prototyp, Fr-Abend):** Ein Server in einer Datei baut aus JSON einfache Teile (Box, Zylinder, Profil; Bohrungen und Taschen oben oder unten), prüft sie und exportiert STEP. Eigene Stärken: Vergleich zweier Extraktionen, Plausibilitätsregeln, FreeCAD-Gegenprüfung, Anleitung für Windows. 34 Tests.
- **Kern (`Archiv.zip` auf Abus Rechner):** Paket `d2c/` mit Zeichnung lesen (rendern, Textebene, Vektormessung, Maßstabsprüfung), `PartSpec` mit Review-Pflicht, Op-Liste, Sandbox, Prüfungen, Toleranzen, AP242-Export mit PMI und Farben, MCP-Server mit 14 Tools, Web-Oberfläche. Volllauf im Image am 17.10. um 09:35: 239 Tests grün, 15 übersprungen (die brauchen BCT-Daten).

Der Plan nimmt den Kern als Basis und übernimmt die Stärken des Prototyps (WP-00, WP-19). Der MCP-Server (WP-01) und das Backend der Web-Oberfläche (WP-08) haben Code und grüne Tests, aber noch kein Review gegen diese Spec. Das Frontend der Web-Oberfläche ist erst ein Gerüst. Noch nicht begonnen: Demo-Befehl, Agent, Auswertung, Teile-Läufe, Compose, README und CI, finales Deck.

## 2. Wie wir gewinnen

Die Wertung hat zwei Hälften. Eine KI prüft Code, Architektur und Baubarkeit (zusammen 50 %, Einzelgewichte nicht veröffentlicht). Die Jury bewertet den Pitch (50 %), das Deck liest sie vorab.

| Prüfer und Kriterium | Was er sehen will | Unser Baustein | Paket |
| --- | --- | --- | --- |
| KI: Baubarkeit | README-Befehle laufen auf frischem Linux | `docker build`, `pytest`, `python -m d2c.demo` grün ohne BCT-Daten und ohne Key, amd64 und arm64 | WP-03, WP-14 |
| KI: Architektur | klare Schichten, begründete Entscheidungen | Op-Liste, Sandbox, MCP-Schicht und Agent getrennt; `docs/architecture.md` | WP-14 |
| KI: Code-Qualität | Typen, Tests, CI | pydantic-Schemas, Tests pro Modul, CI-Workflow | alle, WP-15 |
| KI und Jury: Anforderungen | jede Muss- und Kann-Anforderung belegt | Anforderungsmatrix im README | WP-14 |
| Jury: Ergebnis | funktioniert mit echten Daten | Benchmark gegen die BCT-Referenzen | WP-09 bis WP-12 |
| Jury (BCT): Vertrauen | keine stillen Annahmen | Review-Liste, orange Flächen im STEP, Rückverfolgung jedes Werts, zwei Extraktionen im Vergleich, FreeCAD-Gegenprüfung | WP-01, WP-02, WP-19 |
| Jury: Extras | AP242-PMI, PMI-Prüfung, Toleranzen, lokale Modelle | je ein Baustein mit Beleg | WP-02, WP-07, WP-13 |
| Jury: Pitch | klare Geschichte, Demo, Zahlen | 5-Minuten-Skript, Demo Sensoraufnahme, Video, Deck | WP-17 |

**Alleinstellung** gegenüber anderen Teams derselben Challenge:

1. Gemessene Ergebnisse gegen die BCT-Referenzen für jedes versuchte Teil, auch für die gescheiterten.
2. Fehlende Maße werden aus den Vektoren gemessen, begründet und im STEP markiert (Sensoraufnahme: Breite 20,000 mm).
3. Falsche Schriftfeld-Maßstäbe: In 4 von 21 Zeichnungen passt das Schriftfeld nicht zur Geometrie. `measure_drawing` schlägt den echten Maßstab vor; wie oft ein Lauf das als `conflict` meldet, zählt WP-13.
4. Jeder Op-Wert, der einem gedruckten Maß entspricht, ist an dessen id gebunden. Werte ohne Maß stehen in der Review-Liste. Daraus folgt der Umbau auf Toleranzmitte ohne Handarbeit.
5. Modellunabhängig: Der Server bedient jeden MCP-Client, der Agent jeden OpenAI-kompatiblen Endpunkt. Ganz im Haus bleibt die Zeichnung erst, wenn auch die Lesephase lokal läuft. WP-07 misst beide Phasen und berichtet ehrlich.

**Grenze:** Einen Sieg garantiert niemand. Deshalb sichern wir zuerst, was ganz in unserer Hand liegt: Baubarkeit, Muss-Ziele (M1–M3), Pitch-Deck. Danach kommen die Extras, sortiert nach Nutzen pro Stunde.

## 3. Rollen nach Stärke

Jedes Paket nennt das passende Profil. Die Verteilung ist ein Vorschlag, jeder wählt selbst.

| Profil | Stärken | Typische Pakete |
| --- | --- | --- |
| **A: Core** | Python, Architektur, CadQuery/OpenCascade, Tests | WP-00, WP-01, WP-02, WP-03 Teil 2, WP-07, WP-15, WP-18, Reviews |
| **B: KI und Agent** | LLM-APIs, Prompting, MCP, async Python | WP-04, WP-05, WP-06, Teile-Läufe WP-09 bis WP-12 |
| **C: Frontend und DevOps** | HTML/CSS/JS, Starlette, Docker, GitHub Actions | WP-03 Teil 1, WP-08, WP-14, Reviews |
| **D: Konstruktion, QA und Pitch** | technische Zeichnungen lesen, Toleranzen, CAD-Viewer/NX, Präsentieren | WP-13, WP-16, WP-17, QA in WP-09 bis WP-12 |

Vorschlag: Abu übernimmt Profil A, weil der Kern von Abu stammt. WP-07 liegt bei A, weil die lokalen Modelle auf Abus Mac laufen (36 GB RAM, Ollama mit Qwen-Modellen). WP-19 übernimmt, wer den ersten Prototyp gebaut hat. B, C und D verteilen die anderen unter sich. Teile-Läufe (WP-09 bis WP-12) kann jeder starten, der WP-05 eingerichtet hat. Ein Lauf braucht 10 bis 30 Minuten und läuft im Hintergrund, während man am eigenen Paket weiterarbeitet.

**Last bis G4 (Sa 24:00), geschätzt:** A etwa 8,5 h plus Reviews, B etwa 6 h plus Läufe, C etwa 7,5 h plus Reviews, D etwa 6 h plus QA. Wer früher fertig ist, nimmt Teile-Läufe oder QA.

## 4. Paket-Board

Status: `offen` → `in Arbeit` → `Review` → `fertig`.

| WP | Titel | Profil | Aufwand | Braucht vorher | Gate | Stand 10:15 |
| --- | --- | --- | --- | --- | --- | --- |
| WP-00 | Repos zusammenführen, Image bauen, Arbeitsweise | A + Kristian | 45 min | Kickoff | G0 | offen |
| WP-01 | MCP-Server prüfen und härten (14 Tools, Modell-Anleitung) | A | 1 h 45 min | WP-00 | G1 | Code da, 20 Tests grün |
| WP-02 | Toleranzmitte und AP242-PMI im Export | A | 2,5 h | WP-00; nur Schritt 5 nach WP-01 | G3 | Module da, nicht eingebunden |
| WP-03 | Teil 1 Demo-Befehl, Teil 2 MCP über HTTP | Teil 1 C, Teil 2 A | 1 h + 0,5 h | Teil 1 WP-00, Teil 2 WP-01 | Teil 1 G2, Teil 2 G3 | offen |
| WP-04 | Referenz-Auswertung und Replay | B | 1 h | WP-00 | G1 | offen |
| WP-05 | Isolierte Teile-Läufe einrichten | B | 45 min | WP-00 | G1 | offen |
| WP-06 | Modellunabhängiger Agent | B | 4 h | WP-01 (HTTP-Test erst nach WP-03 Teil 2) | G3 | offen |
| WP-07 | Lokale Modelle messen (auf Abus Mac) | A | 2 h | WP-06 | G4 | offen |
| WP-08 | Web-Oberfläche: Frontend, Compose, Bild-Upload | C | 2 h + 2 h | WP-00; Compose nach WP-03 Teil 2; Live-Läufe nach WP-06; 3D-Ansicht nach WP-02 | Teil 1 G2, Teil 2 G3 | Backend da, 10 Tests grün; Frontend nur Gerüst |
| WP-09 | Teile M1: Sensoraufnahme, Fuss, Welle | B + D | 2 h | WP-05 (Auswertung: WP-04) | **G2** | offen |
| WP-10 | Teile-Bündel A (5 einfache Teile) | B + D | 3 h | WP-09 | G3 | offen |
| WP-11 | Teile-Bündel B (6 mittlere Teile) | B + D | 4 h | WP-09 | G4 | offen |
| WP-12 | Teile-Bündel C (7 schwere Teile) | B + D | 5 h | WP-09 | G5 | offen |
| WP-13 | QA: Review-Listen, NX-Check, NIST-Prüfung, Ergebnistabelle | D | laufend | WP-09 | G2 bis G5 | offen |
| WP-14 | Teil 1 CI, Teil 2 README, Architektur, Frisch-Klon-Prüfung | C | 20 min + 2,5 h | Teil 1 WP-00; Teil 2 WP-03 Teil 1, WP-08 | Teil 1 G1, Teil 2 G4 | offen |
| WP-15 | Qualitäts-Gate: Gesamt-Review vor dem Freeze | A | 1,5 h | WP-01 bis WP-08, WP-19 | G4 | offen |
| WP-16 | BCT-Kontakt: NX-Check, Zusatzdaten, Freigabe | D | 1 h, verteilt | – | sofort, NX bis Sa 15:15 | offen |
| WP-17 | Pitch: Storyline, Deck, Video, Generalprobe | D + zweiter Sprecher | 6 h, verteilt | Zahlen aus WP-13 | G1, G4, G5, Proben So 09:30 und 10:30 | Entwurf da |
| WP-18 | Freeze und Abgabe | A reicht ein, Kristian Ersatz | 1 h | alles | Tag So 10:30, Abgabe 11:15 | offen |
| WP-19 | Stärken des ersten Prototyps übernehmen | Prototyp-Autor (B oder D) | 3 h | WP-00 (Tool: WP-01) | G3 | offen |

## 5. Abhängigkeiten

```
WP-00 --+--> WP-05 (Läufe) --------> WP-09 (M1, G2) --> WP-10, WP-11, WP-12 --> WP-13 --> WP-17 (Zahlen)
        +--> WP-04 (Auswertung) ----> wertet WP-09 bis WP-12 aus
        +--> WP-14 Teil 1 (CI, G1)
        +--> WP-01 (Server-Review) --+--> WP-03 Teil 2 (HTTP) --> WP-08 Compose
        |                            +--> WP-06 (Agent) --> WP-07 (lokale Modelle), WP-08 Live-Läufe
        +--> WP-02 (AP242, Toleranzmitte; nur der letzte Schritt nach WP-01)
        +--> WP-03 Teil 1 (Demo) ---------------+
        +--> WP-08 Teil 1 (Ergebnis-Browser) ---+--> WP-14 Teil 2 (README, G4)
        +--> WP-19 (Prototyp-Stärken; das Tool erst nach WP-01)
WP-16 (BCT-Kontakt) und WP-17 (Storyline) starten sofort, ohne Code.
WP-15 (Gate) nach WP-01 bis WP-08 und WP-19. WP-18 (Abgabe) zuletzt.
```

**Kritischer Pfad:** WP-00 → WP-05 → WP-09. Der Server-Code des Kerns ist getestet. Die Teile-Läufe warten deshalb nicht auf das Review in WP-01. Ändert WP-01 danach etwas am Bau, baut `scripts/replay.py` (WP-04) die Teile aus `ops.json` neu, ohne neue Modell-Sitzung.

**Gleiche Dateien** (Konfliktgefahr):

- `d2c/server.py`, `d2c/instructions.md`, `tests/test_server.py`: WP-01, WP-03 Teil 2 (nur der Start), WP-02 (nur `export_step`), WP-19 (ein neues Tool). **Merge-Reihenfolge genau so:** WP-01, WP-03 Teil 2, WP-02, WP-19. Jeder holt vor dem PR den Stand von `dev` (`git rebase origin/dev`). Die Tool-Namen stehen in `tests/test_server.py` genau einmal, in der Menge `TOOLS`.
- `pyproject.toml`: nur WP-08 (package-data). `Dockerfile`, `.gitignore`, `.dockerignore`, `.gitattributes`: nur WP-00.
- `README.md` und `docs/` im Repo: nur WP-14 (C). Andere liefern Text an C.
- `docs/results.md`: nur WP-13 (D). Wer ein Teil rechnet, meldet die Zeile an D.

## 6. Zeitplan und Gates

| Gate | Zeit | Muss stehen |
| --- | --- | --- |
| Kickoff | Sa 10:30 | Plan gelesen, Pakete verteilt, Entscheidungen aus Abschnitt 8 getroffen, BCT-Kontakt (WP-16) geht sofort los |
| G0 | Sa 11:15 | WP-00: Kern auf `dev`, jedes Teammitglied hat gebaut und die Suite grün gesehen |
| G1 | Sa 13:00 | WP-01, WP-04 gemergt; CI läuft (WP-14 Teil 1); WP-05 eingerichtet, erste M1-Läufe laufen; NX-Check 1 bei BCT erledigt oder fest vereinbart (WP-16); Storyline steht (WP-17) |
| **G2** | **Sa 15:00** | **Muss-Ziel: 3 Teile als STEP, alle Prüfungen grün, Sensoraufnahme mit markierter Breite (WP-09).** Demo-Befehl (WP-03 Teil 1), Ergebnis-Browser (WP-08 Teil 1). NX-Check 2 mit den M1-Teilen bis spätestens 15:15 |
| G3 | Sa 20:00 | Toleranzmitte und AP242 im Export (WP-02), HTTP (WP-03 Teil 2), Agent (WP-06), Web mit Compose und Live-Läufen (WP-08), Prototyp-Stärken (WP-19), Bündel A (WP-10) |
| G4 | Sa 24:00 | Bündel B (WP-11), lokale Modelle (WP-07), README (WP-14 Teil 2), Qualitäts-Gate (WP-15), Deck mit echten Zahlen (WP-17). **Danach keine neuen Funktionen mehr.** |
| G5 | So 09:00 | Bündel C soweit möglich (WP-12), Ergebnistabelle final (WP-13), Video (WP-17). **Freeze.** |
| Proben | So 09:30, 10:30 | zwei Generalproben mit Stoppuhr (WP-17); Tag `v1.0` auf GitHub bis 10:30 (WP-18) |
| Abgabe | So 11:15 | Abu reicht ein, Kristian ist Ersatz (WP-18). Puffer bis 11:30 |
| Pitch | So ab 13:00 | finales PDF hochgeladen (bis 11:00), Demo bereit |

**BCT ist nur bis Sa 15:15 vor Ort.** So steht es auf der Challenge-Seite, für Sonntag ist keine BCT-Zeit eingetragen. Alles, was BCT sehen, prüfen oder freigeben soll, passiert davor: Fragen direkt nach dem Kickoff, NX-Check 1 mit dem Demo-STEP bis etwa 12:30, NX-Check 2 mit den ersten BCT-Teilen ab 14:30 (WP-16).

**Schnittlinien**, falls ein Gate reißt:

- G1 nach 14:00: Die M1-Läufe starten mit dem ungeprüften Server-Stand aus WP-00. Das Review aus WP-01 folgt, danach `replay.py`.
- G2 nach 17:00: WP-07 misst nur ein lokales Modell; WP-10 rechnet nur 3-Loch-Hebel, Schnittmenge und Rotation.
- G3 nach 22:00: WP-06 nur mit Skript-Tests und einem lokalen Modell, keine Live-Läufe in der Web-Oberfläche; WP-08 ohne Bild-Upload.
- G4 nach 02:00: WP-15 nur auf End-to-End-Korrektheit; WP-12 ohne Komplexe Zeichnung 1 und 2 (als ehrliche Grenze im README).
- **Nie gekürzt:** Baubarkeit (J1), Tests, README, Muss-Ziel M1–M3, Review-Liste mit Markierung, Pitch-Deck.

**Schlaf in zwei Schichten:** Schicht 1 von 00:30 bis 04:30 (wer nicht pitcht), Schicht 2 von 04:30 bis 08:30 (die beiden Sprecher). So betreut immer jemand die Teile-Läufe (WP-12). Der Rechner mit laufenden Läufen bleibt wach (auf dem Mac `caffeinate -dims` im Terminal).

## 7. Arbeitsregeln

**Einrichten** (jeder, etwa 20 Minuten plus Build)

1. Docker: auf dem Mac OrbStack oder Docker Desktop, unter Windows Docker Desktop mit WSL2-Backend.
2. Unter Windows vor dem Klonen: `git config --global core.autocrlf false`. Das Repo legt die Zeilenenden zusätzlich per `.gitattributes` fest (WP-00).
3. Klonen, `git switch dev`, `docker build -t d2c .` (beim ersten Mal 5 bis 15 Minuten, Image etwa 2,9 GB).
4. Suite einmal laufen lassen (Befehl unten).
5. BCT-Daten: Jeder lädt sie selbst über den Link auf der Challenge-Seite (nur für zugeteilte Teams, die Links laufen schnell ab) und legt im Repo-Ordner `data/`, `drawings/` und `tables/` an. Die Befehle und die Umlaut-Prüfung stehen in `spec.md` Abschnitt 5.9. Die Ordner stehen in `.gitignore` und `.dockerignore`.
6. `.env` nur lokal, Vorlage `.env.example` (kommt mit WP-06). Den Team-Key gibt es nur auf der Team-Seite der Plattform.

**Git**

- Repo `https://github.com/KristianKalfic/DRAWING2CADMSN`, Arbeitsbranch `dev`. Pro Paket ein Branch `wp-NN-kurzname` von `dev`. Alle vier brauchen Schreibrechte (Kristian lädt als Collaborator ein).
- Vor dem PR den Stand holen: `git fetch origin` und `git rebase origin/dev`.
- Merge nach `dev` per Pull Request. Eine andere Person reviewt und mergt, sobald die CI grün ist (die CI kommt bis G1 mit WP-14 Teil 1, bis dahin zählt die lokale Suite). Review-Paare: C reviewt A, A reviewt B und C, C reviewt D. Ein Review dauert höchstens 20 Minuten. Ist niemand frei und liegt das Paket auf dem kritischen Pfad, mergt der Autor selbst, das Review folgt danach. `main` bekommt erst in WP-18 einen Fast-Forward und den Tag `v1.0`.
- Commit-Nachrichten im Stil Conventional Commits (`feat(server): …`, `fix(web): …`, `docs: …`). Commits nur unter eigenem Namen. **Keine Co-Author-Zeilen, keine Erwähnung von KI-Werkzeugen** in Commits, PRs, Code oder Doku.
- Nur eigene Dateien committen (`git add <pfad>`), nie `git add -A`. Kein `--force` auf geteilte Branches.
- Nie committen: `.env` und Keys; BCT-Dateien und alles daraus Erzeugte (`data/`, `drawings/`, `tables/`, `results/`, `tests/test_bct.py`, `*.stp`, `*.step`, `*.prt`, `*.jt`, Bilder von BCT-Zeichnungen); diesen Plan (`docs/team/`).

**Tests**

- Mac und Linux, aus dem Repo-Ordner: `docker run --rm --network none -v "$PWD":/app d2c pytest -q -p no:cacheprovider tests/`
- Windows PowerShell: `docker run --rm --network none -v "${PWD}:/app" d2c pytest -q -p no:cacheprovider tests/`
- Mit eingehängtem Repo-Ordner läuft der eigene Code ohne neuen Build. Neu bauen nur, wenn sich `Dockerfile` oder `pyproject.toml` ändern.
- Die BCT-Tests stehen in `tests/test_bct.py`. Die Datei ist nicht im Repo, weil sie BCT-Maße enthält; Abu gibt sie lokal weiter (WP-00). Liegen sie und `data/` im Repo-Ordner, laufen sie mit (der Ordner erscheint als `/app/data`). Ohne `data/` werden sie übersprungen, nicht rot.
- Jedes Code-Paket schreibt zuerst die Tests aus seiner Paketdatei, dann den Code. Ausgabe ohne Warnungen.
- Tests ohne BCT-Daten nutzen die Fixtures unter `tests/fixtures/`.

**Code**

- Englisch in Code, Kommentaren, Docstrings, Tool-Beschreibungen, README. Planungsdokumente auf Deutsch.
- Typ-Hinweise, kleine Funktionen, kein toter Code. Neue Abhängigkeiten nur nach Absprache im Team.
- Fehlermeldungen so schreiben, dass ein Sprachmodell sie versteht und sich korrigieren kann.

**Schnittstellen**

- Die Verträge in `spec.md` Abschnitt 5 gelten. Wer einen ändern muss, sagt es im Team-Chat an und passt `spec.md` an (Plan-Pflege, Abschnitt 8).

**Definition of Done** (jedes Code-Paket)

1. Alle Abnahmekriterien der Paketdatei erfüllt.
2. Eigene Tests grün, komplette Suite grün, keine Warnungen.
3. PR reviewt und nach `dev` gemergt.
4. Board-Status auf `fertig`, ein Satz Ergebnis im Team-Chat.

**Kommunikation**

- Kurzes Status-Update zu jedem Gate: fertig, als Nächstes, blockiert durch.
- Blocker sofort melden, nicht erst am Gate. Nach 30 Minuten Festhängen fragen: zuerst im Team, dann die BCT-Mentoren (Jannik Schüle für KI und MCP, Jan Müllerleile für CAD und NX). Beide sind nur bis Sa 15:15 vor Ort.

## 8. Beim Kickoff zu entscheiden

1. **Wo liegt dieser Plan?** Nicht im öffentlichen Repo (siehe oben) und nicht im Team-Chat, denn er enthält BCT-Maße. Empfehlung: Abu gibt `docs/team/` lokal weiter (AirDrop, USB-Stick oder Kabel) und pflegt die Dateien. Das Board (nur Paketnamen und Status) führt eine Person im Team-Chat. Ergebnisse aus BCT-Teilen wandern genauso nur lokal (WP-05, Sammelstelle).
2. **Historie bereinigen?** 11 der 13 Commits im Team-Repo stammen nicht von einem Teammitglied, sondern von einem automatischen Autor-Konto. `git log --format='%an <%ae>' | sort | uniq -c` zeigt es. Das Repo ist öffentlich, Jury und KI-Prüfer sehen es. Bereinigen heißt: zuerst eine Sicherung (`git clone --mirror`), dann den Autor dieser Commits auf die echte Person umschreiben und Force-Push auf `main`. Danach klonen alle neu. Das entscheidet Kristian vor WP-00, solange noch niemand darauf aufbaut. Ab sofort gilt für alle: Commits nur unter eigenem Namen.
3. **Schreibrechte:** Kristian lädt alle als Collaborator ein, `dev` wird Arbeitsbranch, Merge per Pull Request.
4. **Wer hat den ersten Prototyp gebaut?** Diese Person nimmt WP-19. Vorschlag für den Freeze: `prototype/` verlässt `main`, der Tag `prototype-v0` hält ihn sichtbar fest (WP-18). Ein zweiter Server mit älterer MCP-Version sieht für den KI-Prüfer sonst nach totem Code aus.
5. **Wer pitcht?** Zwei Sprecher, eine Person bedient die Demo.
6. **Wer hat einen Windows-Rechner** mit Excel (NIST STEP File Analyzer, WP-13) und wer FreeCAD (WP-19)?
7. **Wer hält den Kontakt zu BCT** (WP-16)? Diese Person geht direkt nach dem Kickoff los.
8. **Wer kann Teile-Läufe fahren?** Gebraucht wird ein Desktop-MCP-Client oder ein Headless-Client mit eigenem Zugang zu einem Vision-Modell (WP-05). Wer das hat, nennt es beim Kickoff. Bis der Agent steht (WP-06, G3), rechnen nur diese Personen; danach jeder über den Agenten mit dem Team-Key.
