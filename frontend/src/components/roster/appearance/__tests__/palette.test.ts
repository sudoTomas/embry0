import { describe, expect, it } from "vitest";
import {
  CAPE_FILL, CAPE_SHADE, CLOTH, COLOURS, GOLD, HAIR, HIDE, LEATHER, MAGE, METAL, OUTLINE, ROBE, SKIN, dark, light, mix, tones,
} from "../palette";

const HEX = /^#[0-9a-f]{6}$/;

describe("appearance palette", () => {
  it("names the tones the brief asks for, every one a six-digit hex", () => {
    expect(Object.keys(SKIN)).toEqual(["light", "tan", "brown", "dark", "stone"]);
    expect(Object.keys(HAIR)).toHaveLength(6);
    expect(Object.keys(CLOTH)).toHaveLength(8);
    expect(Object.keys(METAL)).toEqual(["bronze", "silver", "gold", "black", "platinum", "diamond", "obsidian", "crimson", "divine"]);
    expect(Object.keys(LEATHER)).toEqual(["plain", "green", "blue", "red", "black"]);
    expect(Object.keys(MAGE)).toEqual(["novice", "violet", "high"]);
    for (const table of [SKIN, HAIR, CLOTH, METAL, LEATHER, MAGE, HIDE]) {
      for (const [name, hex] of Object.entries(table)) expect(hex, name).toMatch(HEX);
    }
    expect(GOLD).toMatch(HEX);
    expect(OUTLINE).toMatch(HEX);
  });

  it("merges hair and cloth into one colour-slot vocabulary without a duplicate name", () => {
    expect(Object.keys(COLOURS)).toEqual([...Object.keys(HAIR), ...Object.keys(CLOTH)]);
    expect(new Set(Object.keys(COLOURS)).size).toBe(14);
  });

  it("derives a dark and a light tone from a base, staying in hex", () => {
    expect(mix("#000000", "#ffffff", 0.5)).toBe("#808080");
    expect(dark("#808080")).toMatch(HEX);
    expect(light("#808080")).toMatch(HEX);
    expect(parseInt(dark("#808080").slice(1, 3), 16)).toBeLessThan(0x80);
    expect(parseInt(light("#808080").slice(1, 3), 16)).toBeGreaterThan(0x80);
  });

  it("builds a colour key of three base tones, three trim tones and the outline", () => {
    const key = tones("#9c6b2f", "#e3b94b");
    expect(Object.keys(key).sort()).toEqual(["1", "2", "3", "4", "5", "6", "o"]);
    expect(key["2"]).toBe("#9c6b2f");
    expect(key["5"]).toBe("#e3b94b");
    expect(key["1"]).toBe(dark("#9c6b2f"));
    expect(key["6"]).toBe(light("#e3b94b"));
    expect(key.o).toBe(OUTLINE);
    expect(Object.keys(tones("#9c6b2f")).sort()).toEqual(["1", "2", "3", "o"]);
  });

  it("gives the two ladders seven steps each, every robe a body and a trim (progression spec 2026-10-24)", () => {
    expect(Object.keys(HIDE)).toHaveLength(7);
    expect(Object.keys(ROBE)).toHaveLength(7);
    for (const table of [ROBE]) {
      for (const [name, { base, trim }] of Object.entries(table)) {
        expect(base, name).toMatch(HEX);
        expect(trim, name).toMatch(HEX);
      }
    }
  });

  it("gives the cape the party colour token, the only non-hex colour", () => {
    expect(CAPE_FILL).toBe("var(--sprite-fill)");
    expect(CAPE_SHADE).toContain("var(--sprite-fill)");
  });
});
