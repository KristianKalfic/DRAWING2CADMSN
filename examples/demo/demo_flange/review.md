# Review list: D2C Demo Flange

- Part: demo_flange
- Drawing: flange_drawing.pdf (page 1)
- Scale: 1:1
- General tolerance: ISO 2768-m

## Assumptions and open items
| ID | Dimension / feature | Issue | Assumption | Reason | Source | Marked faces in STEP |
|---|---|---|---|---|---|---|
| rv1 | t8 | missing | Flange thickness 8 mm | The flange thickness is not dimensioned; measured in the front view at scale 1:1 | - | Faces: 2 |

## Drawing notes
- n1: Material S235JR
- n2: Deburr all edges

## Traceability
| Op | Parameter | Value | Dimension | Printed text |
|---|---|---|---|---|
| 0 | profile.d | 60 | d60 | Ø60 |
| 0 | distance | 8 | t8 |  |
| 1 | d | 12 | d12 | Ø12 H7 |
| 2 | d | 5.5 | d55 | 4x Ø5,5 |
| 2 | polar.pitch_d | 44 | d44 | Ø44 |

## Values without dimension reference
No entries.

## Dimensions without model reference
Found in model: 4 of 4 printed length, diameter and radius dimensions (tolerance 0.05 mm; assumed dimensions, angles and count dimensions excluded). The following dimensions appear nowhere in the body and are unconfirmed.

No entries.

## Tolerances
| Dimension | Printed text | Nominal | Minimum | Maximum | Tolerance midpoint | Source | Lower deviation | Upper deviation | Within tolerance (nominal) | Within tolerance (midpoint) |
|---|---|---|---|---|---|---|---|---|---|---|
| d60 | Ø60 | 60 | 59.7 | 60.3 | 60 | general tolerance ISO 2768 | -0.3 | 0.3 | yes | yes |
| d12 | Ø12 H7 | 12 | 12 | 12.018 | 12.009 | fit ISO 286 | 0 | 0.018 | yes | yes |
| d55 | 4x Ø5,5 | 5.5 | 5.4 | 5.6 | 5.5 | general tolerance ISO 2768 | -0.1 | 0.1 | yes | yes |
| d44 | Ø44 | 44 | 43.7 | 44.3 | 44 | general tolerance ISO 2768 | -0.3 | 0.3 | yes | yes |
| t8 |  | 8 | 7.8 | 8.2 | 8 | general tolerance ISO 2768 | -0.2 | 0.2 | yes | yes |

## PMI in the STEP file
| Type | Value | Tolerance | Faces | Description |
|---|---|---|---|---|
| diameter | 12 | H7 | cylinder d=12 at (0, 0, 4) | - |
| diameter | 5.5 | - | cylinder d=5.5 at (15.556, 15.556, 4) | - |
| diameter | 5.5 | - | cylinder d=5.5 at (-15.556, 15.556, 4) | - |
| diameter | 5.5 | - | cylinder d=5.5 at (-15.556, -15.556, 4) | - |
| diameter | 5.5 | - | cylinder d=5.5 at (15.556, -15.556, 4) | - |
| distance | 8 | - | plane z=0, plane z=8 | ASSUMPTION: Flange thickness 8 mm - The flange thickness is not dimensioned; measured in the front view at scale 1:1 - please check |

## Geometric tolerances
None.

## Export notes
None.

## Checks
Consistency with the captured specification: the model is compared with the captured expected values (`expected`), not with the drawing. Whether a printed dimension appears in the body is listed in the section "Dimensions without model reference".

| Check | Expected | Actual | Passed |
|---|---|---|---|
| Valid solid | yes | yes | yes |
| Number of solids | 1 | 1 | yes |
| Free edges | 0 | 0 | yes |
| Volume mm³ | > 0 | 20954.4 | yes |
| Overall size X | 60 | 60 | yes |
| Overall size Y | 60 | 60 | yes |
| Overall size Z | 8 | 8 | yes |
| Hole (d12) | Ø12 at [0.0, 0.0, 0.0], axis [0.0, 0.0, 1.0] | Ø12, 0 from axis, through | yes |
| Hole (d55) | Ø5.5 at [15.5563, 15.5563, 0.0], axis [0.0, 0.0, 1.0] | Ø5.5, 6.95597e-05 from axis, through | yes |
| Hole (d55) | Ø5.5 at [-15.5563, 15.5563, 0.0], axis [0.0, 0.0, 1.0] | Ø5.5, 6.95597e-05 from axis, through | yes |
| Hole (d55) | Ø5.5 at [-15.5563, -15.5563, 0.0], axis [0.0, 0.0, 1.0] | Ø5.5, 6.95597e-05 from axis, through | yes |
| Hole (d55) | Ø5.5 at [15.5563, -15.5563, 0.0], axis [0.0, 0.0, 1.0] | Ø5.5, 6.95597e-05 from axis, through | yes |
| STEP re-import valid | yes | yes | yes |
| Solids in STEP re-import | 1 | 1 | yes |
| Volume change on STEP re-import | <= 1e-06 | 3.87e-14 | yes |
| STEP PMI read back | 6 | 6 | yes |
| Geometric tolerances read back | 0 | 0 | yes |
| Marked faces rv1 | > 0 | 2 | yes |

All checks passed: yes (Consistency with the captured specification, no comparison with the drawing)
