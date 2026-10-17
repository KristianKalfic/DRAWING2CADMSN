# WP-04: Referenz-Auswertung und Replay

| Profil | Aufwand | Braucht vorher | Gate |
|---|---|---|---|
| B (oder A) | 1 h | WP-00 | G1, Sa 13:00 |

## Ziel

1. `eval_refs.py` vergleicht jedes erzeugte Modell mit der BCT-Referenz und schreibt eine Tabelle. Daraus kommen die Benchmark-Zahlen für Pitch und README.
2. `replay.py` baut alle gespeicherten Teile neu. Damit ist bewiesen, dass jedes Ergebnis aus `ops.json` reproduzierbar ist, und frühe Teile bekommen spätere Export-Funktionen ohne neue Modell-Sitzung.

## Kontext

Fertig: `d2c.checks.metrics(shape)` → `{"volume", "bbox_sorted", "cyl_diameters"}`, `d2c.sandbox.run_worker(payload)`, `d2c.session.Session`, `results_root()`. Unabhängig vom MCP-Server, kann also sofort starten.

## Dateien

`scripts/eval_refs.py`, `scripts/replay.py`, `tests/test_scripts.py`.

## Aufgaben

1. `eval_refs.py`:
   - vergleicht jedes `<results_root()>/<T>/<T>.step` mit der einzigen `*.stp` **ohne** `_PMI` im Ordner `<D2C_REF_DATA>/<erste Pfadkomponente von spec.drawing>/`,
   - `D2C_REF_DATA` (Standard `/app/data`) wird bei jedem Aufruf gelesen,
   - Tabellenkopf (Englisch, geht später ins README): `Part`, `ΔV %`, `ΔBBox max mm`, `Missing Ø`, `Extra Ø`, `MATCH`,
   - MATCH nach `spec.md` Abschnitt 6: Volumenabweichung höchstens 1,0 %, sortierte BBox je Achse höchstens 0,05 mm, Zylinder-Ø-Mengen gleich (auf 0,01 mm gerundet),
   - eine ungültige Referenz (Komplexe Zeichnung 2) wird trotzdem verglichen, MATCH zeigt dann „reference invalid“,
   - schreibt die Tabelle nach stdout und nach `<results_root()>/EVAL.md`, die Zeilen als Liste nach `<results_root()>/EVAL.json` mit genau diesen Schlüsseln: `part`, `dv_pct`, `dbbox_max_mm`, `missing_d`, `extra_d`, `match` (`"MATCH"`, `"DIFF"` oder `"reference invalid"`), `volume`, `bbox_sorted`, `ref_volume`, `ref_bbox_sorted`, `ref_valid`. Die Web-Oberfläche verknüpft die Zeilen über `part` (`spec.md` 5.5),
   - Exit-Code immer 0, die Tabelle ist reine Information.
2. `replay.py [--export] [teil ...]`:
   - baut jedes `<results_root()>/*/ops.json` (oder nur die genannten Teile) mit `expected` aus `spec.json` neu, eine Zeile je Teil mit `ok`,
   - Exit-Code 1, wenn ein Teil nicht `ok` ist,
   - mit `--export` stattdessen `Session().export(teil)` (frische Session, lädt von der Platte).

## Tests (zuerst schreiben)

Alle Referenzen erzeugen die Tests selbst in tmp (CadQuery-Box mit Bohrung als STEP), keine BCT-Daten.

- `test_eval_identical_is_match`: Referenz gegen eine Kopie ihrer selbst.
- `test_eval_detects_missing_diameter`: Ergebnis ohne Bohrung → „Ø fehlend“ nennt den Durchmesser.
- `test_eval_resolves_folder_with_spaces`: Ordner `Komplexe Zeichnung 3` mit `1 763 1487 0.stp` und Köder `1 763 1487 0_PMI.stp` → nimmt die Datei ohne `_PMI`.
- `test_replay_fails_on_bad_part`.
- `test_replay_export_rewrites_step`: Demo-Teil exportieren, `.step` löschen, `replay.py --export demo_bracket` → `.step` wieder da, Exit-Code 0.
- `test_eval_json_keys`: `EVAL.json` hat genau die Schlüssel oben, `part` ist der Teilname.
- `test_eval_bct_fuss_reference` mit `@pytest.mark.bct`: Die Referenz `Fuss/Fuss.stp` lädt, ist gültig und hat ein Volumen größer 0. Keine Maße aus den BCT-Daten in den Test schreiben (Veröffentlichung noch nicht freigegeben, WP-16).

## Abnahme

- Tests grün. Ein Lauf gegen den echten `data/`-Ordner funktioniert (ohne Ergebnisse ist eine leere Tabelle richtig).
- Aufruf steht im Team-Chat: `docker run --rm --network none -v "$PWD":/app d2c python scripts/eval_refs.py`.

## Fallen

- Die Referenzen liegen nie im Server-Container. Dieses Skript läuft nur in Entwicklung und QA.
- `scripts/` ist kein Paket. Die Tests rufen die Skripte als Subprozess auf (`python scripts/eval_refs.py`) und setzen `D2C_RESULTS` und `D2C_REF_DATA` auf tmp.
- Ausgaben von `eval_refs.py` gehen nie in eine Modell-Sitzung. Ein Lesefehler wird als „Maß X prüfen“ gemeldet, ohne Referenzwert (WP-09).
- Kantenrundungen erzeugen Zylinderflächen. Deren Ø zählen im MATCH mit.
