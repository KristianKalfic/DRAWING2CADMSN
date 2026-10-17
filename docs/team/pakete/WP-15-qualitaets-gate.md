# WP-15: Qualitäts-Gate: Gesamt-Review vor dem Freeze

| Profil | Aufwand | Braucht vorher | Gate |
|---|---|---|---|
| A (Core), Review durch eine zweite Person | 1,5 h | WP-01 bis WP-08, WP-19 | G4, Sa 24:00 |

## Ziel

Ein letzter Blick auf den ganzen Branch `dev`, bevor nur noch Fehler behoben werden. Der KI-Prüfer bewertet Code und Architektur. Was hier auffällt, findet er auch.

## Aufgaben

1. **End-to-End:** frischer Klon, `docker build`, komplette Suite, `python -m d2c.demo`, `docker compose up` mit einem Live-Lauf (OpenRouter, Demo-Zeichnung), `scripts/replay.py` über alle Teile.
2. **Drei Blickwinkel**, jeweils mit Fundliste (Datei:Zeile, Problem, Vorschlag, Schwere Kritisch/Wichtig/Klein):
   - **Architektur:** Schichten sauber getrennt? Toter Code, doppelte Logik (z. B. zwei Bohrungserkennungen), Module zu groß?
   - **Sicherheit:** Pfadprüfungen in Server und Web, Sandbox-Grenzen, keine Secrets, kein Netz im Server-Container, Uploads begrenzt.
   - **Korrektheit:** Stimmen README-Behauptungen mit dem Code? Liefert jede Fehlermeldung genug, damit ein Modell sich korrigieren kann?
3. Kritisches und Wichtiges sofort beheben (eigene kleine PRs), Kleines in eine Liste für nach dem Hackathon.
4. Bekannte kleine Punkte aus früheren Reviews mit erledigen, wenn billig:
   - Prüfen, dass WP-01 den nackten `TypeError` bei `refs`-Werten, die kein String sind, behoben hat.
   - Docstring von `ExpectedHole`.
5. Spuren-Prüfung aus WP-14 einmal auf dem ganzen Branch `dev`.

## Abnahme

- Fundliste im PR-Text, alle Kritischen und Wichtigen erledigt.
- End-to-End grün.
