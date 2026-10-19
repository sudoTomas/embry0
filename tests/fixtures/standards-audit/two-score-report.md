Score: 83
Hygiene: 25

## Findings

| Severity | Rule | Location | Evidence | Proposed fix |
|---|---|---|---|---|
| high | type-scale | src/styles/app.css:12 | `font.mono` is not held: no font-family use starts with its first family; 5.0 conformance points | set `font-family: var(--font-mono)` on code, ids and hashes |
| medium | type-scale | src/components/Pill.tsx:8 | `font-size: 11px` is off the scale: 47 of 311 font-size uses (15%); 1.5 conformance points | use the `th` step, 12px |
| medium | shell | src/styles/app.css:40 | `layout.header_height` has no use: the header is 57px; 2.5 conformance points | set the header to `var(--header-height)` |
| medium | tokens-only | src/pages/Board.tsx:210 | 343 raw colour literals outside the tokens, 24 per 1,000 UI lines; 28 hygiene points | paint through the role tokens |
| low | one-accent | src/pages/Board.tsx:731 | `#ff69b4` is a saturated hue in no family: 6 of 206 accent and off uses; 0.6 conformance points | use the accent or a status role |

## Scores

| Part | Measured | Weight | Points lost |
|---|---|---|---|
| palette | 16 of 16 colour roles used | 20 | 0.0 |
| accent | 198 of 206 accent and off uses | 20 | 0.8 |
| type | sizes 58% on the scale, 1 of 2 font roles held | 20 | 9.2 |
| radii | 107 of 168 radius uses in the set | 10 | 3.6 |
| shell | 5 of 6 shell values used | 15 | 2.5 |
| tables | 48 tables, 4 card grids | 15 | 1.2 |
| raw colours | 24 per 1,000 UI lines | 40 | 28.3 |
| off-scale sizes | 16 of 21 distinct sizes | 20 | 15.2 |
| off-set radii | 11 of 13 distinct radii | 20 | 16.9 |
| extra families | 3 of 4 named families | 20 | 15.0 |

## Run

The commit, 14,403 UI lines, the standard as given.
