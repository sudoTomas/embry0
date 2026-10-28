# Art direction: one brand from embry0.ai to the dashboard, and the world at night

## Why

embry0 had three looks on one product. embry0.ai is a launch site: a pure black page, cool white text, hairlines, D-DIN set bold and uppercase, Roboto Mono for data, gold the one accent. The dashboard had a blue-teal ground, Outfit and JetBrains Mono, cyan focus rings and pixel page titles. The world (the guild hall and the map) is full-colour daylight pixel art that lights up a dark page like a window. Each was fine alone. Together they read as two products, and the game competed with the work.

The game stays. The characters, the creatures and the nostalgia are part of what makes the guild fun. The aim is one light for all three.

## Decisions

1. **One base: the site's.** The dashboard takes embry0.ai's ink and type:
   - a `#000000` page, `#08080a` surfaces and `#141418` overlays;
   - cool white text `rgb(240 240 250)` in three strengths (100 %, 72 %, 52 %) and hairline rules at 14 % and 30 % of it;
   - gold `#d4af37` the one accent, and the focus ring in the text colour;
   - D-DIN (Datto, SIL OFL 1.1) for words, self-hosted from `frontend/public/fonts/d-din/` with its licence; Roboto Mono for data, code and numbers.

   `frontend/src/index.css` holds the tokens.
2. **Page titles are the site's.** `.title-page` is D-DIN bold, uppercase, tightly set. The product's name keeps its lowercase inside it. No page title is set in the pixel face.
3. **Numbers are telemetry.** `.hud`, used for levels, costs and counts, is Roboto Mono with tabular figures. No number is set in the pixel face.
4. **The pixel face is for the game's nouns only**: a character's or an achievement's name (`.title-pixel`) and the nameplate under a sprite. `theme.test.ts` and `userFacingText.test.ts` hold the rule.
5. **Nightfall: the world after dark.** The art is re-lit, not redrawn. One colour matrix (`frontend/src/components/world/art/relight.ts`):
   - brings saturation to 45 % and light to 58 %;
   - tints red and green down and blue up, with a floor of blue in the shadows.

   It applies in two places that read alike:
   - as the SVG filter `#nightfall` on every sheet sprite and character inside a scene;
   - as a pixel pass over the terrain canvas once it is painted.

   Effects (experience, level-ups, hitsplats, loot sparkle) are light and stay as drawn. Creatures shown in lists outside a scene stay lit: the page is lit, the world is at night.
6. **Gold light falls where the work is.** Torchlight is a static pool of gold added to what is under it (`mix-blend-mode: plus-lighter`, `screen` where unsupported). It falls in these places:
   - **The hall:** its two torches and the quest board, plus the loot chest while it holds loot.
   - **The map:** the hall's door, and every party out on a quest, which carries its light as it walks. A beaten party's light goes out.

   The light never flickers: gold moves only with what it marks.
7. **Dark frames with a gold inlay.** A scene sits in a black frame, a dark gold border with a gold hairline inside it (`.night-frame`), in place of a plain card.
8. **The site meets the guild.** embry0.ai keeps its technical hero. Further down, a section introduces the agents as characters in Nightfall. It shows a still of the real world and states only what the code does, like every sentence on the site.

## What it costs

- Small pixel art is dimmer at night. Status that matters stays in the dashboard's own marks: badges, counts, the "?" bubble, nameplates.
- D-DIN has a regular and a bold. Medium and semibold weights fall to those two.
- The relight is a filter on each sprite of a scene and one pixel pass per terrain paint, so it adds no new image files and has no per-frame cost on the canvas.
