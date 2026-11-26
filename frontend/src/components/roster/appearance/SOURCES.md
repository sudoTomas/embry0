# Sources of the roster art

The roster's adventurers are composed in the browser from the shapes below:
palette-keyed text grids, the body kit in `parts.ts` and the equipment in
`items.ts`, compiled to SVG. No image file is involved. A shape is drawn once
over a colour key, so the items that reuse it in other colours or metal tiers
are not listed apart. Every shape the code draws has its row, under the name
`shapes.ts` gives it, and every row a shape: `__tests__/shapes.test.ts` holds
the table to the code.

Licence: the roster art was drawn for embry0 and ships under the MIT licence with the rest of the repository (`LICENSE`); see `THIRD_PARTY_NOTICES.md`, Images.

How each shape was made is recorded from the git history of `main` (#178),
not from memory. For each shape the table gives the pull request whose tree
first held it exactly as it is now, under any name, and the one that first
gave it its current name (#201 renamed the gear and the rares without
redrawing them):

- **#87** (`9d5c832b`, 2026-10-20) drew the body kit and the equipment
  catalogue. Its commits describe each drawing pass and name no image
  generator: "drawn in #87".
- **#121** (`8b96121b`, 2026-10-24) added four shapes, for the ladders and the
  rares: the open helm, the paper crown, the harvest mask and the cape's
  drape. Its commits say what they draw but not how they were made, so none of
  them is kept.
- **#201** (`e65bbca5`, 2026-10-28) renamed shapes; it drew none.
- **#718** (2026-11-25) redrew #121's four by hand, in this repository, as new
  grids made for embry0, not derived from #121's or from any game's sprites.
  Each keeps its name, its slot, its colour keys and its size, so nothing that
  wears it changes: "redrawn by hand in #718".

| Shape | File | As it is now since | Named so since | How it was made |
|---|---|---|---|---|
| `HEAD_TOP` | `parts.ts` | #87 | #87 | drawn in #87 |
| `EYES` | `parts.ts` | #87 | #87 | drawn in #87 |
| `NOSE` | `parts.ts` | #87 | #87 | drawn in #87 |
| `MOUTH` | `parts.ts` | #87 | #87 | drawn in #87 |
| `HEAD_BOTTOM` | `parts.ts` | #87 | #87 | drawn in #87 |
| `HAIR_STYLES.short` | `parts.ts` | #87 | #87 | drawn in #87 |
| `HAIR_STYLES.spiky` | `parts.ts` | #87 | #87 | drawn in #87 |
| `HAIR_STYLES.long` | `parts.ts` | #87 | #87 | drawn in #87 |
| `HAIR_STYLES.mohawk` | `parts.ts` | #87 | #87 | drawn in #87 |
| `HAIR_STYLES.curly` | `parts.ts` | #87 | #87 | drawn in #87 |
| `HAIR_STYLES.fringe` | `parts.ts` | #87 | #87 | drawn in #87 |
| `BEARDS.goatee` | `parts.ts` | #87 | #87 | drawn in #87 |
| `BEARDS.full` | `parts.ts` | #87 | #87 | drawn in #87 |
| `BEARDS.moustache` | `parts.ts` | #87 | #87 | drawn in #87 |
| `BEARDS.long` | `parts.ts` | #87 | #87 | drawn in #87 |
| `TORSO` | `parts.ts` | #87 | #87 | drawn in #87 |
| `LEGS` | `parts.ts` | #87 | #87 | drawn in #87 |
| `FEET` | `parts.ts` | #87 | #87 | drawn in #87 |
| `ARMS.down` | `parts.ts` | #87 | #87 | drawn in #87 |
| `ARMS.up` | `parts.ts` | #87 | #87 | drawn in #87 |
| `ARMS.slump` | `parts.ts` | #87 | #87 | drawn in #87 |
| `FULL_HELM` | `items.ts` | #87 | #87 | drawn in #87 |
| `CRESTED_HELM` | `items.ts` | #87 | #87 | drawn in #87 |
| `WIZARD_HAT` | `items.ts` | #87 | #87 | drawn in #87 |
| `BANDED_HAT` | `items.ts` | #87 | #87 | drawn in #87 |
| `GEMMED_HAT` | `items.ts` | #87 | #87 | drawn in #87 |
| `HOOD` | `items.ts` | #87 | #201 | drawn in #87 |
| `TRICORNE` | `items.ts` | #87 | #87 | drawn in #87 |
| `MINING_HELMET` | `items.ts` | #87 | #87 | drawn in #87 |
| `OPEN_HELM` | `items.ts` | #718 | #201 | redrawn by hand in #718 |
| `PAPER_CROWN` | `items.ts` | #718 | #201 | redrawn by hand in #718 |
| `HARVEST_MASK` | `items.ts` | #718 | #201 | redrawn by hand in #718 |
| `CHESTPLATE` | `items.ts` | #87 | #201 | drawn in #87 |
| `PAULDRONS` | `items.ts` | #87 | #87 | drawn in #87 |
| `TRIMMED_CHESTPLATE` | `items.ts` | #87 | #201 | drawn in #87 |
| `TRIMMED_PAULDRONS` | `items.ts` | #87 | #87 | drawn in #87 |
| `ROBE_TOP` | `items.ts` | #87 | #87 | drawn in #87 |
| `JERKIN` | `items.ts` | #87 | #87 | drawn in #87 |
| `STRAPPED_BODY` | `items.ts` | #87 | #87 | drawn in #87 |
| `STRIPED_SHIRT` | `items.ts` | #87 | #87 | drawn in #87 |
| `TRIMMED_LEGS` | `items.ts` | #87 | #87 | drawn in #87 |
| `SKIRT` | `items.ts` | #87 | #87 | drawn in #87 |
| `BANDED_SKIRT` | `items.ts` | #87 | #87 | drawn in #87 |
| `RUNED_SKIRT` | `items.ts` | #87 | #87 | drawn in #87 |
| `HEATER_SHIELD` | `items.ts` | #87 | #201 | drawn in #87 |
| `AMULET` | `items.ts` | #87 | #87 | drawn in #87 |
| `PENDANT` | `items.ts` | #87 | #87 | drawn in #87 |
| `CAPE` | `items.ts` | #87 | #87 | drawn in #87 |
| `TRIMMED_CAPE` | `items.ts` | #87 | #87 | drawn in #87 |
| `CAPE_DRAPE` | `items.ts` | #718 | #121 | redrawn by hand in #718 |
| `SABRE` | `items.ts` | #87 | #201 | drawn in #87 |
| `GREATSWORD` | `items.ts` | #87 | #87 | drawn in #87 |
| `STAFF` | `items.ts` | #87 | #87 | drawn in #87 |
| `ORB_STAFF` | `items.ts` | #87 | #87 | drawn in #87 |
| `GLASS_STAFF` | `items.ts` | #87 | #201 | drawn in #87 |
| `SHORTBOW` | `items.ts` | #87 | #87 | drawn in #87 |
| `LONGBOW` | `items.ts` | #87 | #87 | drawn in #87 |
| `CROSSBOW` | `items.ts` | #87 | #87 | drawn in #87 |
| `AXE` | `items.ts` | #87 | #87 | drawn in #87 |
| `PICKAXE` | `items.ts` | #87 | #87 | drawn in #87 |
