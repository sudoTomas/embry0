# The adventurer roster: avatars built like an MMO player

**Status:** implemented with this spec, 2026-10-20
**Workstream:** 3 of 3 (game UI); extends section 3.2 of [the game UI design](2026-10-18-game-ui-design.md)
**Depends on:** `frontend/src/lib/agentIdentity.ts` and `docs/contracts/identity.json` (workstream 2)

## 1. Goal

A second set of agent avatars, defined the way an old-school MMO defines a player rather than drawn one by one: a **body kit** (skin, hair, beard and four colour slots) plus **equipment slots**, rendered by layering the parts. Presets run from a level-3 starter in a plain shirt to maxed-out warriors, mages and archers, each a different person at a glance. The twelve drawn avatars of the first roster stay as they are; the new ids join them in the identity contract, the agent form and the API's validation.

The characters must read as that kind of player avatar: a tall, slim, slightly stiff figure seen from the front, big hands and feet, chunky gear, a full helm that reads as a full helm, a kiteshield that is a kite, a scimitar that curves, robes that drape, a wizard hat that points, a bow that is a bow. Every tier has its own colour so the progression from starter to maxed reads on the sheet without labels.

**Non-goals.** Animating anything beyond the six sprite states; an appearance editor in the dashboard (presets are fixed); per-agent tinting of gear (only the cape wears the party colour); any image file or runtime dependency.

## 2. Design

`frontend/src/components/roster/appearance/`, one module with clear seams:

| file | holds |
|---|---|
| `types.ts` | `Appearance`, `EquipmentSlot`, `Frame`, `Layer`, the style unions, `GRID = 32` |
| `palette.ts` | the named colours (skin ×4, hair ×6, cloth ×8, eight metal tiers, five leathers, three mage orders, gold, wood, string), `mix`/`dark`/`light`, and `tones(base, trim?)`, the three-tone colour key every shape is drawn over |
| `parts.ts` | the body kit: `head(skin, face)`, seven hair styles, four beards, the shirt, trousers, boots, and the arms in three poses (the right arm is `mirror` of the left) |
| `items.ts` | the equipment catalogue, `ITEMS`, keyed by id; `ItemId` is its key type |
| `compose.ts` | `POSES` per sprite state, `layersOf`, `paint`, `runLength`, `compose`, `composeState` |
| `presets.ts` | `PRESETS`, the named appearances, typed by `ADVENTURER_AVATARS` so the two lists cannot drift |
| `index.ts` | the public surface and `COMPOSED`, every preset in every state composed once at module load |

Imports inside the module carry the `.ts` extension: `scripts/adventurer_sheet.mts` runs it under Node 22, which strips the types itself but does not resolve a bare `./palette`.

### 2.1 The appearance definition

```ts
type EquipmentSlot = "head" | "cape" | "neck" | "weapon" | "body" | "shield" | "legs" | "hands" | "feet";

interface Appearance {
  skin: "light" | "tan" | "brown" | "dark";
  hair: "short" | "spiky" | "long" | "mohawk" | "bald" | "curly" | "fringe";
  beard: "goatee" | "full" | "moustache" | "long" | null;
  colours: { hair: ColourId; torso: ColourId; legs: ColourId; feet: ColourId };
  equipment: Partial<Record<EquipmentSlot, ItemId>>;
}
```

`ColourId` is one vocabulary of fourteen names (six hair, eight cloth), so a hair colour is a valid trouser colour and the reverse. Colours are fixed per part and per item, never tinted by the agent's palette colour; the one exception is the **cape**, whose two tones are `var(--sprite-fill)` and a darker mix of it, so the party colour still shows the way a team cape does.

A **frame** is a cropped text grid placed on the 32 by 32 cell canvas: `{ x, y, rows }`, where `.` is transparent, `x` erases what lower layers drew (a hat's crown clears the spikes under it while the hair at the sides still falls) and any other character is looked up in the layer's colour key. The figure stands about 14 wide and 30 tall, feet on the last row, row 0 free for the bob and the spark: hair on rows 1 to 4, head 2 to 10, torso 11 to 19, belt and legs 20 to 28, feet 29 to 31. Arms are on x 7..10 and 21..24; the weapon hand is the left (viewer's left) one, the shield arm the right.

An **item** is `{ id, slot, layer?, weapon?, shoulders?, colours, over?, sleeves?, hands? }`. Most items draw one `layer`; a weapon draws `rest` (in the down hand) and `raised` (above the up hand); a platebody also draws `shoulders` over the arms; a body item may set the `sleeves` colour of the arms and a hands item sets the `hands` colour (gloves and vambraces recolour rather than draw, because the arms move). `over: "hair"` means the item covers the hair (coifs); `over: "beard"` covers hair and beard both (full helms). Every shape is drawn once over the three-tone key, so one full helm is seven metal tiers and one robe is three orders of mage.

### 2.2 Composition

`layersOf(appearance, pose)` returns the layers in draw order: cape, legs (part, then item), feet, body, the two arms with their hands, the body item's pauldrons, head, hair unless hidden, beard unless hidden, head item, neck, shield, weapon. `paint` lays them on the grid (a pose's bob shifts everything up one row; the spark and the sweat drop go on last), and `runLength` groups the cells by colour into one SVG path per colour, `compileFrame`'s run-length idea generalised to any grid and colour key, with a run repeated on the rows below merged into one rectangle. `compose` returns `{ size: 32, paths }`.

A **pose** is `{ arms: { left, right }, face, bob, spark, sweat, ghost }`; it moves the arm and weapon layers and sets the face, nothing else. `POSES` gives every sprite state its frames:

| state | frames |
|---|---|
| `idle` | standing; the same shifted up one row |
| `thinking` | eyes closed with a spark on row 0; eyes open with the spark one column over |
| `tool` | left arm up with the weapon raised (the shield stays); the standing frame |
| `done` | both arms up, the weapon raised and the shield lifted with its arm, a smile |
| `failed` | both arms slumped, a frown, a sweat drop |
| `pending` | the silhouette's edge in the dim tint alone |

### 2.3 Presets

Twenty, each an `AvatarId`:

| id | the look |
|---|---|
| `starter` | level 3: brown hair, cream shirt, trousers, no gear |
| `bronze-warrior` … `dragon-warrior` | one per metal tier (bronze, iron, steel, mithril, adamant, rune, dragon), each the full set: full helm, platebody, platelegs, kiteshield, scimitar; the ladder adds metal boots, a cape and an amulet on the way up |
| `champion` | maxed warrior: black plate trimmed in gold, a crested helm, gold gauntlets, a trimmed cape, a two-hander |
| `apprentice` | blue pointed hat and robes, a plain staff |
| `mystic` | banded hat, gold-trimmed robes, an orb staff, an amulet |
| `archmage` | maxed mage: gemmed hat, runed robes, a glowing crystal staff, a cape |
| `scout` | laced leather jerkin and chaps, vambraces, a shortbow |
| `ranger` | green coif, hide body with a quiver strap, chaps, vambraces, a longbow |
| `marksman` | black coif, black hide, a dark bow, a cape |
| `sharpshooter` | maxed archer: red hide trimmed in gold, gold gauntlets, a trimmed cape, a crossbow |
| `monk` | bald, grey goatee, brown robes, a pendant |
| `pirate` | tricorne, striped shirt, long hair and a full beard, a cutlass |
| `skiller` | a woodcutter: curly blonde hair, green shirt, a felling axe |
| `miner` | mining helmet with a lamp, moustache, a pickaxe, metal boots |

Skin, hair style and colour, beard and the cloth under the gear vary across the set, so even the seven tier warriors differ in the hands, the hair that shows under a coif or a hat, and the boots.

### 2.4 Wiring

- `AVATARS` in `frontend/src/lib/agentIdentity.ts` is `[...LEGACY_AVATARS, ...ADVENTURER_AVATARS]`; `embry0/agents/identity.py` mirrors the split and `docs/contracts/identity.json` pins both sides. `isLegacyAvatarId` and `isAdventurerId` narrow the union; `sprites.ts` is keyed by `LegacyAvatarId` alone.
- `Sprite` renders either kind: an adventurer id draws its composed frames from `COMPOSED` with their fixed fills, a legacy id its compiled frames as before; the `viewBox` follows the frame's grid (`0 0 32 32` or `0 0 16 16`). The pending tint constant is shared. `data-avatar`, `data-color`, `data-state`, `data-frames`, the accessible name and the `still` and `decorative` props are unchanged, and `stateFor`/`stateForNode` are untouched.
- The agent form's avatar radiogroup iterates `AVATARS`, so the presets follow the legacy roster, drawn at 32 px in the chosen colour; eight columns.
- `AgentCreateRequest` and `AgentUpdateRequest` validate `avatar` through `validate_avatar`, which reads the same list: a preset id is accepted, an unknown id still answers 422.

## 3. Testing

`frontend/src/components/roster/appearance/__tests__/`:

- `palette.test.ts`: the named tables and their hexes, the tone helpers, the cape token.
- `parts.test.ts`: rectangular frames inside the grid, every character resolving, the faces, the arm poses and the mirror.
- `items.test.ts`: every item keyed by its id in a known slot, frames inside the grid with every character resolving, weapons on the left with the raised layer above the resting one, the shield on the right, `over` on helms and coifs, the cape alone wearing the party token, a full set per tier.
- `compose.test.ts`: draw order; a helm hides hair and beard, a coif the hair, a hat neither; the shield covers the body; tool raises the weapon layer (its box moves up) and its second frame is the rest; done raises both arms, weapon and shield with a smile; failed slumps, frowns and sweats; thinking closes the eyes and moves the spark; the bob; pending as the edge in one tint; the erase cell; `runLength` against `compileFrame` and the merged rectangles.
- `presets.test.ts`: at least sixteen presets with the named ones present; every preset composes in every state with the state's frame count, each frame 32 rows of 32 cells; every item in the slot it is for and every layer colour resolving; no two presets compose to the same idle frame; only the cape carries the party colour; the directory under 120 KB of source.

`Sprite.test.tsx` renders a preset at `viewBox="0 0 32 32"` with fixed fills and the cape in `--sprite-fill`, a legacy avatar at 16, every preset's pending frame as the one dim path, and `still`. `AgentIdentityFields.test.tsx` lists the presets after the legacy roster at 32 px. `agentIdentity.contract.test.ts` pins `ADVENTURER_AVATARS` to `PRESET_IDS` and both to the contract. `tests/unit/agents/test_identity.py` pins the Python mirror and the contract file; `tests/unit/api/v1/test_agents.py` creates an agent with `rune-warrior`, updates one to `champion`, and refuses `iron-wizard`. The contact sheet at `../plans/2026-10-20-adventurer-roster-contact-sheet.svg` is the review surface for the art.

## 4. Deploy notes

Frontend only, plus the identity vocabulary: `embry0/agents/identity.py` grows by the twenty ids and the API accepts them on agent rows. No migration (the `avatar` column is free text validated at the API), no sandbox image rebuild, no new runtime dependency. An older dashboard shown a row wearing a preset id falls back to `golem` (spec 3.10 of the game UI design), so the backend may go first.
