# World art

The art the world view draws (spec `docs/superpowers/specs/2026-10-25-world-design.md`, Art). `manifest.json` names every asset; the renderer reads it and draws a placeholder for anything it does not find, so assets can be added or replaced without touching code. Where each image came from is in `SOURCES.md`.

## manifest.json

```text
version            1
tileSize           32 (px): the map's grid
tilesets.<name>    { image, layout: "corner-4x4", terrains: { "0": <name>, "1": <name> } }
atlases.<name>     { image, width, height }: power-of-two sheets the entries below point into
objects.<name>     { atlas, rect: [x, y, w, h], anchor: {x, y}, bounds: [x0, y0, x1, y1], category }
creatures.<name>   { atlas, frames: [[x, y, w, h], ...], fps, loop, size, anchor, bounds, leadKinds: [...], interaction }
vfx.<name>         { image, frames: [[x, y, w, h], ...], fps, loop, anchor }
```

- **Tilesets** are corner tilesets: a tile is chosen by the terrain at its four corners, `index = 8·NW + 4·NE + 2·SW + 1·SE`, where a corner is 1 when it is the tileset's terrain `"1"`. Tile `index` sits at column `index % 4`, row `index ÷ 4` of the 128×128 sheet. Tile 0 is all terrain `"0"`, tile 15 all terrain `"1"`. Every tileset's grass is the same grass, so a lake, a rock field, a town square or a forest can sit on any grass.
- **Anchor** is the pixel, within the frame, that stands on the map point: the bottom centre of what is drawn (feet, trunk, the base of a building). `bounds` is the drawn part of the frame.
- **Object categories**: `scenery` (trees, rocks, fences, signposts, bushes, flowers), `town` (houses, wells, banners), `building` (the guild hall outside), `station` (where an idle character stands in the hall: anvil, lectern, alchemy table, map table, desk), `hall` (bookshelf, quest board, torch), `loot` (the chest closed, open, and full of gold).
- **Creatures**: `leadKinds` are the lead kinds a creature stands for; `interaction` is what its button says (`attack`, `talk`, `start_quest`, `repair`). Every creature has a five-frame idle loop; the renderer draws hit and defeat itself.
- **Effects**: `xp_drop` (rises over a character as experience lands), `level_up` (fireworks), `loot_sparkle` (loops over a chest), `hitsplat` (a static badge the renderer animates and writes a number on).
