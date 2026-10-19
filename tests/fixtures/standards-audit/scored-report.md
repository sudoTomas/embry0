Score: 72

The repository mostly holds the standard. Two raw colours and a second accent pull the score down.

## Measurements

| Check | Count |
|---|---|
| raw hex colours | 14 |
| font families | 2 |

## Findings

| Severity | Rule | Location | Evidence | Proposed fix |
|---|---|---|---|---|
| high | one-accent | src/styles/app.css:41 | `--accent-2: #22aa88` is a second accent | remove `--accent-2`; use `color.accent` |
| medium | tokens-only | src/components/Table.tsx:18 | `color: #333333` is a raw value where `color.text` exists | `color: var(--color-text)` |
| low | type-scale | src/pages/Home.tsx:7 | `font-size: 13px` is outside the type scale | use the `body` step |

## Notes

The header lockup already links home.
