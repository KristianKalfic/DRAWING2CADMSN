# WP-02: Toleranzmitte und AP242-PMI im Export

| Profil | Aufwand | Braucht vorher | Gate |
|---|---|---|---|
| A (Core), gern mit D für die fachliche Prüfung | 2,5 h | WP-00; nur Schritt 5 (Server) nach WP-01 | G3, Sa 20:00 |

## Ziel

Jeder Export schreibt STEP **AP242** mit semantischem PMI und Farben, eine GLB für den Browser und auf Wunsch eine zweite Variante auf Toleranzmitte. Damit sind C1 (AP242 mit PMI) und X1 (Toleranzen auf Mitte, Einhaltung prüfen) abgedeckt, die beiden stärksten Extras für die Jury.

## Kontext

Die Module sind fertig und reviewt, aber noch nicht in Session und Worker eingehängt:

- `d2c/tolerances.py`: `limits(dim, general)`, `mid_value(dim, general)`, `apply_mode(ops, spec, mode)`, `compliance(spec, ops, report, mode)`. Tabellen aus `D2C_TABLES`.
- `d2c/export.py`: `write_step(shape, path, *, name, marks, pmi, threads)`, `pmi_from(spec, ops, shape, report)`, `write_glb(shape, path, marks, threads)`, `read_pmi(path)`.

Heute exportiert der Worker noch über die einfache Assembly in `checks.step_roundtrip` (AP214 mit Farben). Die Teile aus WP-09 entstehen deshalb zuerst als AP214. Nach diesem Paket exportiert `replay.py --export` (WP-04) sie ohne neue Modell-Sitzung als AP242 neu.

`pmi_from` braucht keine Tabellen: Es nimmt Wert, Abmaße und Passung wie gedruckt aus der Spec. Tabellen braucht nur, was Grenzmaße berechnet (`limits`, `apply_mode`, `compliance`).

## Dateien

`d2c/session.py`, `d2c/review.py`, `d2c/worker.py`, `d2c/checks.py` (nur `step_roundtrip`), `d2c/tolerances.py` (nur `compliance`), `d2c/server.py` (nur `export_step`), `d2c/instructions.md`, `tests/conftest.py` (nur `D2C_TABLES`), dazu die Tests in `tests/test_session.py`, `tests/test_server.py`, `tests/test_export.py`, `tests/test_tolerances.py`.

## Aufgaben

1. **Worker:** Export über `export.write_step` (Schema AP242DIS). Die Nutzlast bekommt optional `"pmi_spec": <PartSpec als dict> | null`. Ist sie gesetzt, berechnet der Worker nach den Prüfungen `pmi_from(...)` und gibt das Ergebnis an `write_step`. `threads` ist ein Eintrag je Gewindebohrung (`kind == "tapped"`: Position global, Bohrrichtung, gebohrter Ø). Ohne `pmi_spec` verhält sich der Worker wie bisher.
2. **Roundtrip:** `roundtrip.pmi = {"written": n, "read_back": m}`, zurückgelesen mit `read_pmi`. `roundtrip.ok` verlangt `m == n`.
3. **Session:** `Session.export(part, variant="nominal"|"mid")`.
   - `mid` baut mit `apply_mode(ops, spec, "mid")` und schreibt `<teil>_mid.step` und `report_mid.json`.
   - Beide Varianten schreiben `compliance` in den Report. Ein toleriertes Maß, an das kein Wert gebunden ist, bekommt dort `in_tolerance: null` (nicht geprüft). Heute steht `true`, weil `all([])` wahr ist; der Docstring von `compliance` sagt es sogar. Tests, die das alte Verhalten festhalten, anpassen.
   - **Ohne Tabellen** (kein `D2C_TABLES`-Ordner mit den BCT-XML, so beim KI-Prüfer und in der CI) bricht der Nominal-Export nicht ab: `compliance` wird `null`, `review.md` schreibt unter Tolerances „tolerance tables not found (D2C_TABLES)“. Nur `variant="mid"` lehnt dann mit genau dieser Meldung ab.
   - Jeder Export schreibt zusätzlich `<teil>.glb`, `marks.json` und `pmi.json`. Ein fehlgeschlagener Export löscht auch diese Dateien und die `_mid`-Dateien.
   - `tests/conftest.py` setzt `D2C_TABLES` per `monkeypatch` auf `tests/fixtures/tables`, wie heute schon `D2C_RESULTS` und `D2C_DATA`.
4. **Review-Liste:** `review.md` bekommt `## Tolerances` (Maß, Text, Nennmaß, unteres und oberes Abmaß, Mitte, Quelle, eingehalten nominal, eingehalten mid; bei `null` steht dort „not checked“) und `## PMI in the STEP file` (Typ, Wert, Toleranz, Flächen, Beschreibung). `review.json` bekommt dieselben Daten unter `tolerances` und `pmi`.
5. **Server:** `export_step(part, variant="nominal")`.
6. **instructions.md:** Die Regel zum Erfassen der Toleranzen bringt schon WP-01. Hier nur ergänzen: `export_step(part, variant="mid")` gibt es, wenn die Zeichnung Abmaße oder Passungen hat.
7. **Demo:** `d2c.demo` setzt `D2C_TABLES` schon selbst auf `tests/fixtures/tables`, wenn die Variable fehlt (WP-03 Teil 1). Prüfen, dass `docker run --rm d2c python -m d2c.demo` nach diesem Paket die Toleranzen ohne BCT-Daten zeigt.

## Tests (zuerst schreiben)

- `test_export_mid_variant`: Demo-Teil → `demo_bracket_mid.step` existiert, der Report zeigt für `d16` (Ø16 H7) den gemessenen Ø 16.009 und `in_tolerance` wahr.
- `test_session_export_ap242_header`: Dateitext enthält `AP242_MANAGED_MODEL_BASED_3D_ENGINEERING_MIM_LF`.
- `test_session_export_writes_glb_and_marks`: `.glb` beginnt mit `glTF`, `marks.json` nennt `rv1`.
- `test_roundtrip_pmi_counts`: `roundtrip.pmi.written == read_back`, mindestens 2 (Ø16 H7 und die angenommene Dicke 10).
- `test_review_has_tolerance_and_pmi_sections`.
- `test_export_without_tables_still_ok`: `D2C_TABLES` auf einen leeren tmp-Ordner → Nominal-Export `ok`, `compliance` ist `null`; `variant="mid"` → Fehler mit „tolerance tables not found“.
- `test_failed_export_removes_glb_and_mid_files`.
- `test_compliance_unbound_dim_is_not_checked`: toleriertes Maß ohne gebundenen Wert → `in_tolerance` ist `None`.

## Abnahme

- Demo-Teil exportiert: STEP AP242 mit `LIMITS_AND_FITS('H','hole','7','')` für Ø16 H7 und `DIMENSIONAL_LOCATION` 10 mm mit Beschreibung `ASSUMED: …` für die Dicke.
- Komplette Suite grün.
- `python scripts/replay.py --export` (WP-04) baut alle schon erzeugten Teile mit dem neuen Export neu.

## Fallen

- Der Sandbox-Worker erbt nur `PATH`, also auch kein `D2C_TABLES`. `apply_mode` und `compliance` laufen deshalb im Session-Prozess. Der Worker bekommt fertige Ops und die Spec als dict.
- Gespeicherte `ops.json` und `spec.json` der Teile-Läufe müssen gültig bleiben. Nur Felder ergänzen, keine umbenennen.
- `AP242DIS` wird erst nach `STEPControl_Controller.Init_s()` angenommen.
- Die Wurzel muss ein Compound sein, sonst liefert `AddSubShape` Null-Labels.
- OCCT schreibt den Passungsgrad um 2 zu hoch, `export.py` gleicht das schon aus. Den Test dazu nicht entfernen.
- Vorzeigeteil für die Jury ist der Getriebedeckel: Ø68 +0,2/0, Ø28 0/−0,05, 16 0/−0,1, 4× Ø6,6 H7. BCTs `Getriebedeckel_PMI.stp` taugt als Vergleich (16 semantische Maße).
