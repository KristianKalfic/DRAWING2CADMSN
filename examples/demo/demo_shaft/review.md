# Review list: D2C Demo Shaft

- Part: demo_shaft
- Drawing: shaft_drawing.pdf (page 1)
- Scale: 1:1
- General tolerance: ISO 2768-m

## Assumptions and open items
No entries.

## Drawing notes
- n1: Material C45
- n2: Deburr all edges

## Traceability
| Op | Parameter | Value | Dimension | Printed text |
|---|---|---|---|---|
| 0 | steps.0.0 | 20 | ds1 | Ø20 |
| 0 | steps.0.1 | 35 | l1 | 35 |
| 0 | steps.1.0 | 30 | ds2 | Ø30 |
| 0 | steps.1.1 | 45 | l2 | 45 |
| 0 | steps.2.0 | 24 | ds3 | Ø24 |
| 0 | steps.2.1 | 30 | l3 | 30 |
| 1 | size | 2 | c2 | 2x45° |

## Values without dimension reference
No entries.

## Dimensions without model reference
Found in model: 7 of 7 printed length, diameter and radius dimensions (tolerance 0.05 mm; assumed dimensions, angles and count dimensions excluded). The following dimensions appear nowhere in the body and are unconfirmed.

No entries.

## Tolerances
| Dimension | Printed text | Nominal | Minimum | Maximum | Tolerance midpoint | Source | Lower deviation | Upper deviation | Within tolerance (nominal) | Within tolerance (midpoint) |
|---|---|---|---|---|---|---|---|---|---|---|
| ds1 | Ø20 | 20 | 19.8 | 20.2 | 20 | general tolerance ISO 2768 | -0.2 | 0.2 | yes | yes |
| ds2 | Ø30 | 30 | 29.8 | 30.2 | 30 | general tolerance ISO 2768 | -0.2 | 0.2 | yes | yes |
| ds3 | Ø24 | 24 | 23.8 | 24.2 | 24 | general tolerance ISO 2768 | -0.2 | 0.2 | yes | yes |
| l1 | 35 | 35 | 34.7 | 35.3 | 35 | general tolerance ISO 2768 | -0.3 | 0.3 | yes | yes |
| l2 | 45 | 45 | 44.7 | 45.3 | 45 | general tolerance ISO 2768 | -0.3 | 0.3 | yes | yes |
| l3 | 30 | 30 | 29.8 | 30.2 | 30 | general tolerance ISO 2768 | -0.2 | 0.2 | yes | yes |
| lt | 110 | 110 | 109.7 | 110.3 | 110 | general tolerance ISO 2768 | -0.3 | 0.3 | yes, checked (geometry) | yes |

## PMI in the STEP file
No entries.

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
| Volume mm³ | > 0 | 56233.5 | yes |
| Overall size X | 30 | 30 | yes |
| Overall size Y | 30 | 30 | yes |
| Overall size Z | 110 | 110 | yes |
| STEP re-import valid | yes | yes | yes |
| Solids in STEP re-import | 1 | 1 | yes |
| Volume change on STEP re-import | <= 1e-06 | 2.57e-14 | yes |
| STEP PMI read back | 0 | 0 | yes |
| Geometric tolerances read back | 0 | 0 | yes |

All checks passed: yes (Consistency with the captured specification, no comparison with the drawing)
