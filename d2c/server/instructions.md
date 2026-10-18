Drawing2CAD turns a vector PDF drawing into a validated STEP model with a review list. Units: mm and degrees, Z up. Planes: XY has normal +Z (u=X, v=Y), XZ -Y (u=X, v=Z), YZ +X (u=Y, v=Z).
Tools carry a prefix (`mcp__d2c__<name>` or `mcp__drawing2cad__<name>`): use the names from the tool list.

Reading the drawing
1. `render_drawing` per page at 100 dpi: determine the views, the title block scale and the notes.
2. `read_drawing_text` per page: `items` are the printed values; you need `printed_dims` and `title_scale` in step 4. Without a text layer, read the image. Stacked tolerances are separate items; slanted text can be split: never conclude from a single fragment.
3. `render_drawing` per view with `crop`, 300-600 dpi.
4. `measure_drawing` per drawing with `printed_dims` (six or more) and the title block scale. If `scale_suggestion` differs or the response reports `scale_check.conflict`, measure with `best` and add a review item `issue="conflict"`.

Capturing the specification
5. Every printed dimension with `text` as printed, `source.cell` (null without a grid) and `confidence`. `scale` as in the title block; `general_tolerance` as "ISO 2768-f|m|c|v" (without K) if the title block or notes state it.
6. A dimension that appears in no view is measured, never estimated (`measure_drawing` with the view region, same drawing and page): `assumed=true`, `assumption_basis` "measured from the vector geometry of the drawing: <value> mm (view <id>, scale <s>)", review item `issue="missing"`. If measuring is impossible: a ratio.
7. Check the main dimensions with `measure_drawing` and `expect` (e.g. `["L=80", "d=30"]`); a `MISMATCH` becomes a review item `issue="conflict"`.
8. Every text line or symbol that is not a single dimension (collective notes such as "unspecified radii R=1", standard references, roughness, edge notes, material) is captured verbatim as a `Note`. Tolerance frames: `geometric_tolerances`. If a collective note determines geometry, apply it and, if assumed, list it as a review item.
9. `expected`: bounding box in model coordinates, every hole with position and axis.
10. Per review item `mark_near` as a point list `[[x, y, z], ...]`, one in the middle of each face that determines the unclear dimension. Assumed length: one point centred on BOTH bounding faces; radius or diameter: one point on the cylindrical face. If available, call `check_spec` once, adopt or fix every finding, then `submit_spec`.

Building the geometry
11. Before building, tick off each feature:
   - Draft: if an angle is given, always use `taper`; a printed dimension takes precedence over a measured coordinate.
   - Countersinks, chamfers, fillets, small holes and thread cores often appear only in detail or section views.
   - Take cavities and hollows from the section views (a solid block is usually wrong); read the base thickness in the section, not from differences.
   - A dimension arrow ending at a centreline counts from the axis, not as an overall length.
   Build the base body first; the order is free afterwards.
   - Round profile corners with `fillets` in the `path` profile, not with `edge_finish`; chamfers and undercuts at shoulders of a turned part are points in the `revolve` profile.
   - `edge_finish` directly after the body whose edges it changes; if `valid=false`, `move_op` it forward, otherwise delete and recreate.
   - Through holes: `add_holes` without `depth`.
   - `run_script` only if no other tool covers the geometry.
12. Follow `next` in every response. Fix `error`, `solid.ok=false` or `roundtrip.ok=false` immediately. `failed` for features not yet built is normal; before `export_step` everything must pass, otherwise `edit_op` or `delete_op` and recreate (at most three times per feature), then a review item. `dims_found.missing` is a hint: check the dimension, correct the model or justify it in the review item. For holes with `through=false` a blind hole is allowed, a closed passage is not.
13. Bind every toleranced dimension with `refs` to the op value that produces it, also as a half dimension or profile coordinate (Ø68 to 34).
14. `export_step`; for deviations or fits additionally `export_step(part, variant="mid")`. Check `roundtrip.pmi.missing`: assumed dimensions without PMI need corrected `mark_near` points.

Rules
- `submit_spec(keep_ops=true)` keeps and renews the ops, without keep_ops everything is deleted. It serves corrections to `expected`, `review`, `mark_near` and new revisions of a part (read `get_state`, `changes`, change only the differences). `assumed` true to false requires a new measurement.
- The axis of a turned part is Z: `expected.bbox` is (D, D, length), the hole axes follow, `revolve` turns about Z.
- Toleranced dimensions (`tol_upper`/`tol_lower` or `fit`) are always bound with `refs` to the operation value, otherwise the tolerance midpoint cannot shift them.
- `mark_near` lies at most 0.5 mm from the face, never on an edge, never inside a hole.
- Threads: an internal thread is `add_holes` with `kind="tapped"`; an external thread is the nominal diameter plus a review item `issue="convention"`.
- Never use the reference STEP files.
- Write free text in English (`title`, `assumption_basis`, `ReviewItem.reason`, `Feature.note`); `Dimension.text` and verbatim quotes (also `Note.text`) unchanged as printed.
