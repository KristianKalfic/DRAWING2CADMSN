# WP-17: Pitch: Storyline, Deck, Video, Generalprobe

| Profil | Aufwand | Braucht vorher | Gate |
|---|---|---|---|
| D (Präsentieren), dazu eine zweite Person als Sprecher | 6 h, über beide Tage verteilt | Zahlen aus WP-13, Bilder aus WP-08 | Storyline G1, Deck G4, Video G5, Generalproben So 09:30 und 10:30 |

## Ziel

50 % der Wertung. Die Jury liest das Deck **vorab**, also muss jede Folie ohne Sprecher verständlich sein. Der Pitch dauert etwa 5 Minuten.

## Was schon da ist

Ein Entwurf mit 10 Folien (HTML und PDF, Abus Rechner unter `~/d2c-pitch/`: `deck.html`, `deck.pdf`, `storyline.md`, Bilder der Sensoraufnahme und des 3-Loch-Hebels). Gestaltung: Hintergrund `#f4f5f6`, Text `#131619`, Akzent Orange `#ef7d1a`, Grün `#3fae58`, Helvetica Neue, eckig, keine Verläufe. Offene Platzhalter: Benchmark-Zahlen, Screenshot der Web-Oberfläche, ehrliche Grenzen, Repo-Link. Der Repo-Link muss auf das Team-Repo `github.com/KristianKalfic/DRAWING2CADMSN` geändert werden.

**Übergabe:** Abu gibt `~/d2c-pitch/` lokal weiter (AirDrop, USB-Stick oder Kabel), nicht über Chat oder Cloud. Der Ordner enthält Bilder aus BCT-Zeichnungen und kommt deshalb auch nie ins Repo. `deck.html` öffnet im Browser, das PDF entsteht über „Drucken als PDF“ (Querformat, ohne Ränder). Wer das Deck bearbeitet, sagt es im Team-Chat an, damit nur eine Fassung entsteht.

## Folien (Kernbotschaft: „Eine KI liest die Zeichnung, ein lokaler Server baut und prüft jedes Maß, jede Annahme ist im Modell markiert.“)

1. **Titel:** „From legacy drawing to a checked STEP model.“ Bild: Zeichnung und markiertes 3D-Modell.
2. **Problem:** 21 Beispielteile; 4 mit falschem Schriftfeld-Maßstab; 1 absichtlich fehlendes Maß. Nachkonstruktion kostet Stunden, stille Annahmen landen in der Fertigung.
3. **Lösung in fünf Schritten:** Lesen (rendern, Text, Vektoren messen) → Beschreiben (PartSpec mit Review-Pflicht) → Bauen (Sandbox, alles neu bei jeder Änderung) → Prüfen (Solid, jedes Maß, STEP-Re-Import) → Exportieren (AP242 mit PMI, Farben, Review-Liste).
4. **Demo Sensoraufnahme:** Breite fehlt → aus den Vektoren gemessen: 20,000 mm → als Annahme mit Grund eingetragen → Seitenflächen orange → semantisches PMI „ASSUMED, please check“, genau wie in BCTs eigener Referenz. Screenshot der Web-Oberfläche.
5. **Ergebnisse:** Jedes versuchte Teil gegen die BCT-Referenz gemessen: x von 21 im Erstlauf MATCH, y nach einem Hinweis aus der Zeichnung, 100 % der exportierten STEPs bestehen die Server-Prüfungen, z Review-Einträge, Minuten und Kosten je Teil. Ehrliche Grenzen: welche nicht und warum.
6. **Vertrauen statt Blackbox:** Rückverfolgung jedes Werts, Werte ohne Maß gesondert gelistet, Widersprüche als `conflict`, kein Export bei roter Prüfung. Zwei Extraktionen im Vergleich (WP-19).
7. **Extras:** Toleranzmitte nach ISO 286/2768 mit BCTs eigenen Tabellen (Getriebedeckel), AP242 mit PMI, geprüft (NIST oder NX), lokales Modell.
8. **Sicherheit und Datenhoheit:** Server ohne Netz, Sandbox mit Grenzen. Was lokal schon läuft, zeigt WP-07: Bauphase mit lokalem Modell, Lesephase je nach Messung.
9. **Nutzen für BCT:** Add-on neben NX und Teamcenter, Stapelkonvertierung von Archiven mit Review-Warteschlange. Ausblick: Form- und Lagetoleranzen als PMI, direkter NX-Import.
10. **Team und Repo:** vier Namen, Repo-Link, Quickstart in drei Befehlen.

## Ablauf der 5 Minuten

| Zeit | Inhalt |
|---|---|
| 0:00–0:40 | Problem, Aufhänger: falscher Maßstab beim 3-Loch-Hebel, automatisch gefunden |
| 0:40–1:20 | Lösung |
| 1:20–3:00 | Demo Sensoraufnahme (live oder Video) |
| 3:00–3:50 | Zahlen und Vertrauen |
| 3:50–4:30 | Extras und Sicherheit |
| 4:30–5:00 | Nutzen für BCT, Schluss |

## Aufgaben

1. **Bis G1:** Storyline mit dem Team abstimmen. Sprecher festlegen (zwei Personen plus eine an der Demo).
2. **Bis G4:** Deck mit echten Zahlen aus WP-13, Screenshot aus WP-08, NX-Beleg aus WP-13/16. Jede Folie verständlich ohne Sprecher. Keine Zahl, die nicht in `docs/results.md` steht.
3. **Bis G5:** Backup-Video 60–90 s (Upload der Sensoraufnahme bis zum markierten STEP, beschleunigt, ohne Ton oder mit kurzen Untertiteln). Die Sprecher schlafen in Schicht 2 (README Abschnitt 6), deshalb Generalproben erst So 09:30 und 10:30, jeweils mit Stoppuhr.
4. **Bis So 11:00:** finales PDF auf der Plattform hochladen (Team-Seite, Bereich Pitch-Deck).
5. **Fragen vorbereiten**, die BCT stellen wird, mit je einer Antwort in einem Satz:
   - Wie gut ist es bei Scans und Bildern? (ehrlich: gemessen nur an Vektor-PDFs; Bilder laufen über das Vision-Modell ohne Vektormessung, Upload nimmt PNG und JPG an)
   - Was kostet ein Teil? (Kosten und Laufzeit aus `docs/results.md`)
   - Wie lange braucht das von Hand? (Zahl von BCT aus WP-16, Frage 4)
   - Läuft es mit NX? (NX-Beleg aus WP-13 und WP-16, sonst ehrlich: Re-Import, FreeCAD, NIST)
   - Was passiert bei einem Fehler im Modell? (Report, Korrektur, Review-Eintrag, kein Export bei roter Prüfung)
   - Woher wisst ihr, dass die Maße stimmen? (Referenzvergleich nach dem Lauf, Referenzen nie in der Sitzung)
   - Wohin gingen die BCT-Zeichnungen? (an das Modell des jeweiligen Laufs, wie die Challenge es erlaubt; nicht ins Repo, zu keinem anderen Dienst; mit lokalen Modellen bleiben sie im Haus, Stand aus WP-07)
   - Was bedeutet die AGPL für uns? (PyMuPDF steckt nur in der Vektormessung und im Bild-Upload. Für ein kommerzielles Add-on: Artifex-Lizenz oder diese zwei Stellen austauschen. poppler läuft als externes Programm, der Rest ist Apache-2.0, MIT oder LGPL)
6. Bilder von BCT-Teilen im Deck: WP-16 Frage 3 klärt, ob das erlaubt ist.

## Abnahme

- PDF hochgeladen, Video liegt lokal und auf einem zweiten Rechner.
- Zwei Generalproben unter 5:00, die zweite bis So 10:45.
