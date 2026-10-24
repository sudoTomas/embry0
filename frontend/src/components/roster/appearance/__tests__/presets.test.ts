import { describe, expect, it } from "vitest";
import composeSrc from "../compose.ts?raw";
import indexSrc from "../index.ts?raw";
import itemsSrc from "../items.ts?raw";
import paletteSrc from "../palette.ts?raw";
import partsSrc from "../parts.ts?raw";
import presetsSrc from "../presets.ts?raw";
import outfitSrc from "../outfit.ts?raw";
import typesSrc from "../types.ts?raw";
import { FRAME_COUNTS, SPRITE_STATES } from "../../sprites";
import { POSES, compose, composeState, layersOf, paint } from "../compose";
import { ITEMS } from "../items";
import { CAPE_FILL } from "../palette";
import { PRESETS, PRESET_IDS } from "../presets";
import { GRID, type Appearance } from "../types";

const presets = Object.entries(PRESETS) as [string, Appearance][];

describe("adventurer presets", () => {
  it("names at least sixteen adventurers, from the starter to a maxed warrior, mage and archer", () => {
    expect(PRESET_IDS.length).toBeGreaterThanOrEqual(16);
    expect(PRESET_IDS).toEqual(Object.keys(PRESETS));
    for (const id of ["starter", "bronze-warrior", "iron-warrior", "steel-warrior", "mithril-warrior", "adamant-warrior", "rune-warrior", "dragon-warrior", "champion", "apprentice", "mystic", "archmage", "scout", "ranger", "marksman", "sharpshooter"]) {
      expect(PRESET_IDS, id).toContain(id);
    }
    for (const id of PRESET_IDS) expect(id).toMatch(/^[a-z]+(-[a-z]+)*$/);
  });

  it("composes every preset in every state with the state's frame count, each frame 32 rows of 32 cells", () => {
    for (const [id, a] of presets) {
      for (const state of SPRITE_STATES) {
        const frames = composeState(a, state);
        expect(frames, `${id}.${state}`).toHaveLength(FRAME_COUNTS[state]);
        for (const frame of frames) expect(frame.size).toBe(GRID);
        for (const pose of POSES[state]) {
          const grid = paint(a, pose);
          expect(grid, `${id}.${state}`).toHaveLength(GRID);
          for (const row of grid) expect(row, `${id}.${state}`).toHaveLength(GRID);
        }
      }
    }
  });

  it("wears every item in the slot it is for, and every layer colour resolves", () => {
    for (const [id, a] of presets) {
      for (const [slot, item] of Object.entries(a.equipment)) {
        expect(ITEMS[item as keyof typeof ITEMS].slot, `${id} wears ${item} as ${slot}`).toBe(slot);
      }
      for (const state of SPRITE_STATES) for (const pose of POSES[state]) {
        for (const layer of layersOf(a, pose)) {
          for (const c of new Set(layer.frame.rows.join("").replace(/[.x]/g, ""))) {
            expect(layer.colours[c], `${id}.${state} ${layer.name} ${JSON.stringify(c)}`).toBeTruthy();
          }
        }
      }
    }
  });

  it("composes no two presets to the same idle frame", () => {
    const seen = new Map<string, string>();
    for (const [id, a] of presets) {
      const key = JSON.stringify(compose(a, POSES.idle[0]).paths);
      expect(seen.get(key), `${id} looks like ${seen.get(key)}`).toBeUndefined();
      seen.set(key, id);
    }
  });

  it("tints only the cape with the party colour", () => {
    for (const [id, a] of presets) {
      for (const state of SPRITE_STATES) for (const pose of POSES[state]) {
        for (const layer of layersOf(a, pose)) {
          const tinted = Object.values(layer.colours).some((c) => c.includes(CAPE_FILL));
          if (tinted) expect(layer.name, `${id}.${state}`).toBe("cape");
        }
      }
    }
    const caped = presets.filter(([, a]) => a.equipment.cape);
    expect(caped.length).toBeGreaterThan(0);
    for (const [id, a] of caped) expect(Object.keys(compose(a, POSES.idle[0]).paths).some((c) => c.includes(CAPE_FILL)), id).toBe(true);
  });

  it("keeps the appearance module under 200 KB of source (progression spec 2026-10-24)", () => {
    const total = [composeSrc, indexSrc, itemsSrc, outfitSrc, paletteSrc, partsSrc, presetsSrc, typesSrc].reduce((n, s) => n + s.length, 0);
    expect(total).toBeGreaterThan(20_000);
    expect(total).toBeLessThan(200_000);
  });
});
