# WP-16: BCT-Kontakt: NX-Check, Zusatzdaten, Freigabe

| Profil | Aufwand | Braucht vorher | Gate |
|---|---|---|---|
| D (Kommunikation) | 1 h, verteilt bis 15:15 | – | erster Kontakt direkt nach dem Kickoff, NX-Check 2 bis Sa 15:15 |

## Ziel

BCT ist Auftraggeber und Jury zugleich. Früher, konkreter Kontakt bringt vier Dinge: einen echten NX-Beleg (M2), möglicherweise Zusatzdaten für die Erweiterung (Folie „Extension for Fast Teams“: „Jan will provide sample data“), Klarheit, was ins öffentliche Repo darf, und einen Kanal für Fragen nach 15:15.

## Zeitfenster

BCT ist laut Challenge-Seite nur **Sa 17.10. 08:00–15:15** vor Ort. Für Sonntag ist keine Zeit eingetragen. Alles in diesem Paket passiert deshalb heute bis 15:15.

## Kontakte

Kontaktdaten auf Folie 2 und 3 der Challenge-Folien.

- **Jan Müllerleile**, Implementation Consultant CAD: NX, Beispieldaten.
- **Jannik Schüle**, Associate AI Consultant: Vision-Modelle, Tool Calling (MCP), KI-Architektur.
- Adin Özder, Sascha Ellmer, Marko Weber.

## Aufgaben

1. **Direkt nach dem Kickoff (bis etwa 11:00):** Jan ansprechen, zwei Sätze zum Stand: „Lokaler MCP-Server, baut und prüft jedes Maß, AP242 mit PMI und farbig markierten Annahmen; die fehlende Breite der Sensoraufnahme wird gemessen und markiert.“ Dann fünf Fragen:
   1. Gibt es zusätzliche Beispieldaten für die Erweiterung (Toleranzen), und dürfen wir sie angehen? Wenn ja: sofort an Profil B (Läufe) und A (WP-02). Ein vorhandenes Teil wird per `edit_op` geändert statt neu gebaut, danach `export_step(variant="mid")`.
   2. Kann Jan heute zweimal kurz STEP-Dateien von uns in NX öffnen: gegen 12:30 ein Demo-Teil, ab 14:30 die ersten BCT-Teile? Zeiten fest ausmachen.
   3. Was darf ins öffentliche Repo: erzeugte Modelle der Beispielteile (z. B. ein Ordner `examples/` mit Spec, Ops, Review-Liste und STEP der drei M1-Teile), Bilder aus den Zeichnungen, Teilenamen mit Kennzahlen (Abweichung zur Referenz), einzelne Maße in Tests (`tests/test_bct.py`, WP-00)? Und was ins Pitch-Deck?
   4. Wie erreichen wir BCT nach 15:15 und am Sonntag (Mail, Chat), falls noch eine Frage oder eine NX-Prüfung offen ist?
   5. Wie lange braucht ein Konstrukteur, um die Sensoraufnahme von Hand nachzumodellieren? Die Zahl geht in den Pitch (WP-17).
2. **NX-Check 1, gegen 12:30:** ein STEP AP242 des Demo-Teils mit PMI und Farben, ohne BCT-Daten. Erzeugen (Profil A, 5 Minuten, kein neuer Code), im Repo-Ordner: `mkdir -p out`, dann `docker run --rm --network none -v "$PWD":/app d2c pytest -q -p no:cacheprovider tests/test_export.py --basetemp=/app/out/nx`. Ohne den Ordner `out/` scheitern alle Tests. Die Datei liegt danach unter `out/nx/export0/demo_bracket.step` (geprüft: AP242, Passung, „ASSUMED“, Farben). Prüfen lassen: öffnet fehlerfrei, Farben sichtbar, PMI sichtbar (Ø16 H7, angenommene Dicke 10 mit „ASSUMED“). Foto oder Screenshot.
3. **NX-Check 2, ab 14:30:** alle bis dahin fertigen M1-Teile (zuerst Sensoraufnahme). Sie sind zu diesem Zeitpunkt AP214 mit Farben, ohne PMI. Mitnehmen: STEP-Dateien auf USB-Stick oder Laptop, `review.md` am Bildschirm. Ergebnis an WP-13.
4. **Rückmeldungen** von BCT (was ihnen wichtig ist, was sie in NX sehen wollen) sofort an WP-17 (Pitch) und an Profil A.
5. **Optional, wenn Zeit ist:** Jannik Schüle zur Zwei-Phasen-Idee befragen (Vision-Modell liest, kleines lokales Modell baut). Die Einschätzung ist gutes Pitch-Material.
6. Antworten kurz in `docs/team/bct-notizen.md` festhalten (privat wie der ganze Plan, kommt nie ins Repo). Eine Freigabe mit Name und Uhrzeit notieren und nach Möglichkeit per kurzer Mail bestätigen lassen.

## Abnahme

- Fünf Fragen beantwortet und notiert.
- NX-Check 1 und 2 stattgefunden oder dokumentiert, warum nicht.
- Freigabe-Antwort an WP-08, WP-13, WP-14, WP-17 und WP-18 weitergegeben (betrifft Screenshot, Ergebnistabelle, README, Deck und Abgabe).

## Fallen

- Ohne Antwort auf Frage 3 gilt: im öffentlichen Repo nur das Demo-Teil und Kennzahlen ohne Maße, keine Modelle und keine Bilder der BCT-Teile.
- Gibt es keinen NX-Termin mehr, ist der Ersatzbeleg für M2: STEP-Re-Import im Server, FreeCAD-Gegenprüfung (WP-19) und NIST-Prüfung (WP-13). README und Pitch sagen dann ehrlich, welche Dateien BCT in NX gesehen hat.
