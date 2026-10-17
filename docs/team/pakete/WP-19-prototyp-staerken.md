# WP-19: Stärken des ersten Prototyps übernehmen

| Profil | Aufwand | Braucht vorher | Gate |
|---|---|---|---|
| wer den Prototyp gebaut hat (laut Historie vermutlich Atakan, Upload-Commit `aqkanat`), sonst B oder D | 3 h | WP-00 (das Tool in `server.py` erst nach WP-01) | G3, Sa 20:00 |

## Ziel

Drei Ideen aus dem ersten Prototyp (`prototype/issue_check.py`) machen R2 („Unklares markieren statt raten“) und M2 („öffnet fehlerfrei in einem CAD-System“) deutlich stärker. Hier ziehen sie auf die `PartSpec` des Kerns um:

1. **Zwei unabhängige Extraktionen vergleichen.** Liest das Modell dieselbe Zeichnung zweimal unabhängig, ist jede Abweichung ein Hinweis auf eine unklare Stelle. Das ist eine Review-Quelle, die ohne Referenz auskommt.
2. **Deterministische Plausibilitätsregeln** auf der Spec, ohne LLM: Bohrung ragt aus dem Körper, Bohrungen überlappen, Radius größer als halbe Kante, niedrige Konfidenz.
3. **Gegenprüfung in einem zweiten CAD-Kern (FreeCAD).** Das exportierte STEP wird in FreeCAD geöffnet und nachgemessen. Das ist ein unabhängiger Beleg für M2, solange kein NX verfügbar ist.

## Kontext

- Vorlage: `prototype/issue_check.py` mit `compare(a, b)`, `plausible(spec)`, `freecad(step, spec, exe)` und Tests in `prototype/tests/test_issue_check.py`. Sie arbeiten auf dem alten JSON-Schema (`base`, `features`).
- Ziel-Schema: `PartSpec` (`spec.md` Abschnitt 5.1) und `Expected`.
- Review-Einträge haben `issue: missing|ambiguous|low_confidence|conflict|convention`.

## Dateien

`d2c/crosscheck.py`, `tests/test_crosscheck.py`, `scripts/freecad_check.py`. Geteilt mit anderen Paketen (nach dem Merge von WP-01, Review durch Profil A): in `d2c/server.py` das neue Tool, in `tests/test_server.py` der Name in `TOOLS`, in `d2c/instructions.md` eine Regel.

## Aufgaben

1. `compare_specs(a: PartSpec, b: PartSpec, tol=0.05) -> list[dict]`
   - Maße paaren über `(kind, gerundeter value)` und gedruckten `text`; Maße, die nur in einer Extraktion vorkommen, melden.
   - `expected.bbox` je Achse vergleichen, `expected.holes` nach gerundeter Position paaren und Ø, Lage und Achse vergleichen.
   - Ergebnis je Abweichung: `{"code": "EXTRACTION_MISMATCH"|"ONLY_IN_A"|"ONLY_IN_B"|"HOLE_COUNT", "message", "where", "suggested_review": {"issue": "ambiguous", ...}}`.
2. `plausibility(spec: PartSpec) -> list[dict]` mit den Regeln aus dem Prototyp, übertragen auf `expected`: Bohrung außerhalb der BBox, Bohrungen überlappen, Rundung größer als halbe kleinste BBox-Kante. Die Regel „niedrige Konfidenz ohne Review-Eintrag“ entfällt, die prüft schon das Schema von `PartSpec`.
3. MCP-Tool `check_spec(spec: PartSpec, second: PartSpec | None = None)` → `{"findings": [...], "count": n}`, ohne die Spec zu registrieren. Ein dict, keine Liste: Eine leere Liste käme beim Client als leere Antwort an. Die Anleitung bekommt die Regel: vor `submit_spec` einmal `check_spec` aufrufen; jeder Befund wird ein Review-Eintrag oder wird behoben. Damit hat der Server 15 Tools: `check_spec` in die Menge `TOOLS` in `tests/test_server.py` aufnehmen und `test_lists_14_tools` in `test_lists_all_tools` umbenennen. Der HTTP-Test aus WP-03 nutzt dieselbe Menge und bleibt so grün.
4. `scripts/freecad_check.py <step> [--spec spec.json] [--freecad PFAD]`: FreeCAD headless (`FreeCADCmd`) öffnet das STEP, misst `isValid`, Anzahl Solids, Volumen, BBox, konkave Zylinder. Mit `--spec` werden BBox und Bohrungen gegen `expected` geprüft. Ohne FreeCAD: klare Meldung und Exit-Code 2 (nicht 1, damit CI es von einem echten Fehler unterscheiden kann). Läuft außerhalb des Containers, FreeCAD ist kein Teil des Images.
5. QA-Lauf (mit WP-13): Für jedes exportierte Teil `freecad_check.py`, Ergebnis als Spalte „FreeCAD“ in `docs/results.md`.

## Tests (zuerst schreiben)

- `test_compare_identical_specs_no_findings` (Demo-Spec gegen sich selbst)
- `test_compare_detects_changed_hole_position`
- `test_compare_detects_dimension_only_in_one`
- `test_plausibility_hole_outside_bbox`
- `test_plausibility_overlapping_holes`
- `test_check_spec_tool_returns_findings` (über den Server, auch der Fall ohne Befund: `count` ist 0)
- `test_freecad_check_without_freecad_exits_2` (Subprozess mit nicht vorhandenem Pfad)

## Abnahme

- Tests grün, komplette Suite grün.
- Ein Beispiel für den Pitch: zwei Extraktionen derselben BCT-Zeichnung mit mindestens einer gefundenen Abweichung, die als Review-Eintrag endet.
- FreeCAD-Spalte in `docs/results.md` für alle exportierten Teile (wer FreeCAD installiert hat).

## Fallen

- Zwei Extraktionen kosten doppelt Tokens. Mit dem Team-Key (13,33 $) sparsam umgehen. Für den Benchmark reicht die zweite Extraktion bei den Teilen, deren Referenzvergleich abweicht.
- FreeCAD unter Windows: `C:/Program Files/FreeCAD 1.0/bin/FreeCADCmd.exe`, unter macOS: `/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd`.
