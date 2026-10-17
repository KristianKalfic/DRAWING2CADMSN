# WP-18: Freeze und Abgabe

| Profil | Aufwand | Braucht vorher | Gate |
|---|---|---|---|
| A (Abu reicht ein), Kristian ist Ersatz und hat die Repo-Rechte | 1 h | alles | Tag bis So 10:30, Abgabe 11:15 (Frist 11:30) |

## Ziel

Eine saubere, nachvollziehbare Abgabe: Branch `main`, Tag `v1.0`, volle SHA, README-Text. Es gibt genau **eine** finale Abgabe, jedes Mitglied kann sie ersetzen. Deshalb gibt nur eine Person ab: Abu, nach Ansage im Team-Chat. Fällt Abu aus, gibt Kristian ab.

## Aufgaben

1. **So 09:00, Freeze:** keine neuen Funktionen mehr, nur noch Fehlerbehebung über PRs mit Review.
2. **Kompletter Lauf** im frischen Klon von `dev`: Suite, Demo, Frisch-Klon-Prüfung aus WP-14 (arm64 und amd64). Auf dem Rechner mit der Sammelstelle (WP-05) zusätzlich `scripts/replay.py` und `scripts/eval_refs.py`.
3. **Aufräumen vor `main`:**
   - Prüfen, dass der private Teamplan nie im Repo war: `git log --all --oneline -- docs/team` ist leer. Ein Fast-Forward nach `main` übernimmt die ganze Historie von `dev`, ein späteres Löschen würde ihn nicht entfernen.
   - Spuren-Prüfung aus WP-14 noch einmal, ohne Befund.
   - Keine BCT-Dateien, keine erzeugten Modelle der BCT-Teile im Repo (außer BCT hat es freigegeben, WP-16). `git ls-files tests/test_bct.py` ist leer.
   - **Prototyp** (Kickoff-Entscheidung 4): zuerst `git tag prototype-v0` auf den letzten Commit mit `prototype/`, dann per PR `git rm -r prototype` und im README der Satz „The first prototype is kept at tag `prototype-v0`.“
   - **Nur mit BCT-Freigabe** (WP-16, Frage 3): `/tests/test_bct.py` aus `.gitignore` nehmen und die Datei committen; `examples/<teil>/` mit `spec.json`, `ops.json`, `review.md` und STEP der drei M1-Teile anlegen, die CI baut sie mit `docker run --rm -e D2C_RESULTS=/app/examples d2c python scripts/replay.py` nach (braucht keine PDF). Ohne Freigabe sagt das README offen, dass die Ergebnisse an BCT-Teilen nur als Kennzahlen veröffentlicht sind.
4. **Bis 10:30:** `dev` nach `main` mergen (Fast-Forward), Tag `v1.0`, Push. CI auf GitHub muss grün sein. Muss danach noch ein Fix hinein: neuer Tag `v1.0.1` und dessen SHA abgeben.
5. **Bis 11:15:** Abgabeformular auf der Plattform (Team-Seite, Bereich Abgabe):
   - Repo-URL `https://github.com/KristianKalfic/DRAWING2CADMSN`
   - Branch `main`, Tag `v1.0`
   - SHA: Ausgabe von `git rev-parse v1.0^{commit}` (40 Zeichen)
   - README-Text: das komplette `README.md` von `v1.0`. Ist das Feld zu kurz: Zweck, Quickstart und Requirement coverage.
6. Screenshot der bestätigten Abgabe in den Team-Chat.

## Abnahme

- Abgabe bestätigt vor 11:30, SHA stimmt mit `v1.0` auf GitHub überein, CI grün.

## Fallen

- Nach dem Tag nichts mehr auf `main` pushen, sonst zeigt der Branch nicht mehr auf die abgegebene SHA.
- Das Repo ist öffentlich. Alles, was auf `main` liegt, sieht die Jury.
