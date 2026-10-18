# Review list: D2C Demo Bracket

- Part: demo_bracket
- Drawing: demo_drawing.pdf (page 1)
- Scale: 1:1
- General tolerance: ISO 2768-m

## Assumptions and open items
| ID | Dimension / feature | Issue | Assumption | Reason | Source | Marked faces in STEP |
|---|---|---|---|---|---|---|
| rv1 | t10 | missing | Plate thickness 10 mm | The plate thickness is not dimensioned; measured in the front view at scale 1:1 | - | Faces: 2 |

## Drawing notes
- n1: Material S235JR
- n2: Deburr all edges

## Traceability
| Op | Parameter | Value | Dimension | Printed text |
|---|---|---|---|---|
| 0 | profile.w | 80 | d80 | 80 |
| 0 | profile.h | 50 | d50 | 50 |
| 0 | profile.corner_radius | 5 | r5 | R5 |
| 0 | distance | 10 | t10 |  |
| 1 | profile.d | 30 | d30 | Ø30 |
| 1 | plane.offset | 10 | t10 |  |
| 2 | d | 16 | d16 | Ø16 H7 |
| 3 | d | 6.6 | d66 | 4x Ø6,6 |

## Values without dimension reference
| Op | Parameter | Value | Note |
|---|---|---|---|
| 1 | distance | 15 | - |

## Dimensions without model reference
Found in model: 9 of 9 printed length, diameter and radius dimensions (tolerance 0.05 mm; assumed dimensions, angles and count dimensions excluded). The following dimensions appear nowhere in the body and are unconfirmed.

No entries.

## Tolerances
| Dimension | Printed text | Nominal | Minimum | Maximum | Tolerance midpoint | Source | Lower deviation | Upper deviation | Within tolerance (nominal) | Within tolerance (midpoint) |
|---|---|---|---|---|---|---|---|---|---|---|
| d80 | 80 | 80 | 79.7 | 80.3 | 80 | general tolerance ISO 2768 | -0.3 | 0.3 | yes | yes |
| d50 | 50 | 50 | 49.7 | 50.3 | 50 | general tolerance ISO 2768 | -0.3 | 0.3 | yes | yes |
| r5 | R5 | 5 | 4.9 | 5.1 | 5 | general tolerance ISO 2768 | -0.1 | 0.1 | yes | yes |
| d30 | Ø30 | 30 | 29.8 | 30.2 | 30 | general tolerance ISO 2768 | -0.2 | 0.2 | yes | yes |
| d16 | Ø16 H7 | 16 | 16 | 16.018 | 16.009 | fit ISO 286 | 0 | 0.018 | yes | yes |
| d66 | 4x Ø6,6 | 6.6 | 6.4 | 6.8 | 6.6 | general tolerance ISO 2768 | -0.2 | 0.2 | yes | yes |
| p60 | 60 | 60 | 59.7 | 60.3 | 60 | general tolerance ISO 2768 | -0.3 | 0.3 | yes, checked (geometry) | yes |
| p30 | 30 | 30 | 29.8 | 30.2 | 30 | general tolerance ISO 2768 | -0.2 | 0.2 | yes, checked (geometry) | yes |
| h25 | 25 | 25 | 24.8 | 25.2 | 25 | general tolerance ISO 2768 | -0.2 | 0.2 | yes, checked (geometry) | yes |
| t10 |  | 10 | 9.8 | 10.2 | 10 | general tolerance ISO 2768 | -0.2 | 0.2 | yes | yes |

## PMI in the STEP file
| Type | Value | Tolerance | Faces | Description |
|---|---|---|---|---|
| diameter | 16 | H7 | cylinder d=16 at (0, 0, 12.5) | - |
| diameter | 6.6 | - | cylinder d=6.6 at (30, 15, 5) | - |
| diameter | 6.6 | - | cylinder d=6.6 at (-30, 15, 5) | - |
| diameter | 6.6 | - | cylinder d=6.6 at (30, -15, 5) | - |
| diameter | 6.6 | - | cylinder d=6.6 at (-30, -15, 5) | - |
| distance | 10 | - | plane z=0, plane z=10 | ASSUMPTION: Plate thickness 10 mm - The plate thickness is not dimensioned; measured in the front view at scale 1:1 - please check |

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
| Volume mm³ | > 0 | 43993.2 | yes |
| Overall size X | 80 | 80 | yes |
| Overall size Y | 50 | 50 | yes |
| Overall size Z | 25 | 25 | yes |
| Hole (d16) | Ø16 at [0.0, 0.0, 0.0], axis [0.0, 0.0, 1.0] | Ø16, 0 from axis, through | yes |
| Hole (d66) | Ø6.6 at [30.0, 15.0, 0.0], axis [0.0, 0.0, 1.0] | Ø6.6, 0 from axis, through | yes |
| Hole (d66) | Ø6.6 at [-30.0, 15.0, 0.0], axis [0.0, 0.0, 1.0] | Ø6.6, 0 from axis, through | yes |
| Hole (d66) | Ø6.6 at [30.0, -15.0, 0.0], axis [0.0, 0.0, 1.0] | Ø6.6, 0 from axis, through | yes |
| Hole (d66) | Ø6.6 at [-30.0, -15.0, 0.0], axis [0.0, 0.0, 1.0] | Ø6.6, 0 from axis, through | yes |
| STEP re-import valid | yes | yes | yes |
| Solids in STEP re-import | 1 | 1 | yes |
| Volume change on STEP re-import | <= 1e-06 | 1.14e-14 | yes |
| STEP PMI read back | 6 | 6 | yes |
| Geometric tolerances read back | 0 | 0 | yes |
| Marked faces rv1 | > 0 | 2 | yes |

All checks passed: yes (Consistency with the captured specification, no comparison with the drawing)
