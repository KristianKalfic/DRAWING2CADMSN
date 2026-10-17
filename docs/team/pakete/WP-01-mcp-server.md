# WP-01: MCP-Server prüfen und härten (14 Tools, Modell-Anleitung)

| Profil | Aufwand | Braucht vorher | Gate |
|---|---|---|---|
| A (Core) | 1 h 45 min | WP-00 | G1, Sa 13:00 |

## Ziel

Ein KI-Modell kann über MCP eine Zeichnung lesen, eine `PartSpec` einreichen, das Teil Schritt für Schritt bauen und als STEP exportieren (R3, „ausschließlich über den MCP-Server“). Der Code dafür steht und ist getestet. Dieses Paket prüft ihn gegen `spec.md`, schließt die Lücken unten und gibt ihn frei.

## Kontext

- **Existiert und ist grün** (aus `Archiv.zip`): `d2c/server.py` (307 Zeilen), `d2c/instructions.md` (13 Schritte plus Regeln), `tests/test_server.py` (20 Tests), Fixtures `tests/fixtures/demo_drawing.pdf`, `demo_spec.json`, `demo_ops.json`.
- Aufbau von `server.py`: `server = MCPServer("drawing2cad", instructions=...)`. `_tool` registriert ein Tool und hüllt es in `_guard`, das `KeyError`, `ValueError` (auch pydantics `ValidationError`) und `IndexError` als `ToolError` mit der Meldung weiterreicht. `_op_tool(Model)` erzeugt die Parameter der Bau-Tools direkt aus den Feldern des Op-Modells. `python -m d2c.server` startet stdio.
- Vorhandene Tests: `test_lists_14_tools`, `test_tool_params_match_op_models`, `test_tool_docstrings_state_conventions`, `test_render_returns_png_and_info`, `test_render_crop`, `test_bad_args_tool_error`, `test_invalid_op_message_reaches_client`, `test_unknown_part_message`, `test_demo_end_to_end`, `test_edit_move_delete_and_state`, `test_run_script_applies_op`, `test_text_tool_demo`, `test_text_tool_no_text_layer`, `test_text_tool_too_many_items`, `test_text_tool_rejects_outside_data`, `test_measure_tool_demo`, `test_measure_tool_scale_note`, `test_measure_tool_notes_missing_scale_and_few_values`, `test_measure_tool_edge_width_as_text`, `test_measure_tool_rejects_outside_data`.
- Die Tool-Namen stehen einmal in `tests/test_server.py` als Menge `TOOLS`.
- Schnittstellen: `spec.md` Abschnitt 5.1 bis 5.4.

## Dateien

`d2c/server.py`, `d2c/instructions.md`, `tests/test_server.py`. Nur falls Schritt 3 es verlangt: `d2c/ops.py` oder `d2c/session.py` (eine Validierung, kein Umbau).

## Aufgaben

1. **Tools gegen `spec.md` 5.4 prüfen:** 14 Namen, Parameter, Rückgaben. `export_step` hat heute nur `part`, `variant` kommt mit WP-02.
2. **Docstrings lesen**, alle 14: ein Satz Zweck, Einheiten (mm, Grad), Ebenen-Konvention (`XZ` hat die Normale −Y), ein Minimalbeispiel. Was fehlt, ergänzen. Der vorhandene Test prüft nur Stichproben.
3. **Fehlerpfade:** Jeder Fehler, den ein Modell auslösen kann, kommt als `ToolError` mit lesbarer Meldung an. Bekannte Lücke aus einem früheren Review: Ein `refs`-Wert, der kein String ist (z. B. eine Liste), endet in einem nackten `TypeError`, der Client sieht dann keinen Text. Ursache finden und dort als `ValueError` mit klarer Meldung melden (oder `refs` im Op-Modell als `dict[str, str]` validieren lassen). `TypeError` nicht pauschal in `_guard` aufnehmen, sonst verdecken sich echte Programmfehler.
3a. **Scans und Bilder (R1):** Findet `measure_drawing` auf der Seite keine Vektorgeometrie, steht in `notes`: „no vector geometry on this page (scan or image?): read sizes from render_drawing, mark them assumed, confidence at most 0.6“. Heute fehlt so ein Hinweis.
4. **`instructions.md` ergänzen** (Englisch, knapp, nummeriert bleiben):
   - `submit_spec` nur einmal pro Teil aufrufen. Ein zweiter Aufruf setzt die Op-Liste zurück. Korrekturen an der Spec vor dem ersten Bau-Tool.
   - Teilnamen genau so verwenden, wie der Auftrag sie nennt.
   - Toleranzen wie gedruckt erfassen: `tol_upper` und `tol_lower` für Abmaße, `fit` für Passungen (z. B. `H7`), `general_tolerance` aus dem Schriftfeld (z. B. `ISO 2768-m`). Jeden Op-Wert, der einem gedruckten Maß entspricht, per `refs` an dessen id binden. Heute erwähnt die Anleitung Toleranzen gar nicht. Ohne diese Regel fehlen den Specs der Teile-Läufe die Daten für die Toleranzmitte (WP-02).
   - Werte ohne Maß: Abgeleitete Werte (z. B. halbe Breite) bleiben ohne `refs`. Ein geschätzter Wert ist eine Annahme und braucht einen Review-Eintrag `missing` mit Grund.
5. **Tests ergänzen** (zuerst schreiben, alle in `tests/test_server.py`):
   - `test_render_rejects_outside_data_and_non_pdf`: `pdf="../../etc/passwd"` und `pdf="Demo Teil (A)/demo.stp"` → `ToolError`. Darauf baut der Isolationstest in WP-05.
   - `test_refs_not_a_string_is_tool_error`: `extrude` mit `refs={"distance": ["d1"]}` → `ToolError` mit einer Meldung, die `refs` nennt.
   - `test_edit_op_bad_index_message`: `edit_op` mit Index 99 → `ToolError`, Meldung nennt den gültigen Bereich.
   - `test_export_with_red_report_writes_nothing`: Demo-Spec einreichen, nur die erste Op bauen, `export_step` → `ok` falsch, kein STEP im Teilordner.
   - `test_measure_tool_raster_page_note`: eine gerasterte Demo-Zeichnung (Seite als Bild gerendert und in ein neues PDF gelegt, im Test mit PyMuPDF erzeugt) → `notes` enthält den Hinweis aus Schritt 3a.
   - `test_stdio_server_lists_tools`: `python -m d2c.server` als Subprozess, Client aus `mcp.client.stdio` (`stdio_client`, `ClientSession`), `list_tools` liefert genau `TOOLS`. Höchstens 30 s, danach Prozess beenden. Die Client-API im Container nachlesen, nicht raten: `docker run --rm d2c python -c "import mcp.client.stdio as m; help(m)"`.
6. **Echter Client:** einmal mit einem MCP-Client gegen das Image prüfen, ob 14 Tools mit lesbaren Beschreibungen erscheinen. Entweder MCP Inspector (`npx @modelcontextprotocol/inspector docker run -i --rm --network none d2c`) oder ein Desktop-MCP-Client mit der Konfiguration aus WP-05.
7. Nach dem Merge: `docker build -t d2c .` und `docker tag d2c d2c:prod`, im Team-Chat ansagen (WP-05 nutzt `d2c:prod`).

## Abnahme

- Neue und alte Tests grün, komplette Suite grün, keine Warnungen.
- PR-Text listet, was geprüft wurde und was sich geändert hat (Docstrings, Fehlermeldungen, Anleitung).
- Ein echter MCP-Client sieht 14 Tools.
- PR gemergt bis G1. Laufende Teile-Läufe (WP-09) brauchen danach nur einen Replay, keine neue Sitzung.

## Fallen

- Import immer `from mcp.server.mcpserver import MCPServer, Image` und `from mcp.server.mcpserver.exceptions import ToolError`. `mcp.server.fastmcp` gibt es in mcp 2.x nicht.
- mcp 2.3 reicht nur die Meldung von `ToolError` an den Client weiter. Alle anderen Ausnahmen kommen dort ohne Text an.
- Der Server lädt CadQuery beim Start (über `d2c.ops`). Etwa 2 s Startzeit sind normal.
- Unter macOS scheitert `RLIMIT_AS` im Sandbox-Worker. Server und Tests laufen deshalb nur im Linux-Container.
- Op-Modelle (`d2c/ops.py`) nicht umbauen. Gespeicherte `ops.json` der Teile-Läufe müssen gültig bleiben, sonst scheitert `replay.py`.
