import { describe, expect, it } from "vitest";
import {
  CAPE_FILL, CAPE_SHADE, CLOTH, COLOURS, GOLD, HAIR, LEATHER, MAGE, METAL, OUTLINE, SKIN, dark, light, mix, tones,
} from "../palette";

const HEX = /^#[0-9a-f]{6}$/;

describe("appearance palette", () => {
  it("names the tones the brief asks for, every one a six-digit hex", () => {
    expect(Object.keys(SKIN)).toEqual(["light", "tan", "brown", "dark"]);
    expect(Object.keys(HAIR)).toHaveLength(6);
    expect(Object.keys(CLOTH)).toHaveLength(8);
    expect(Object.keys(METAL)).toEqual(["bronze", "iron", "steel", "black", "mithril", "adamant", "rune", "dragon"]);
    expect(Object.keys(LEATHER)).toEqual(["plain", "green", "blue", "red", "black"]);
    expect(Object.keys(MAGE)).toEqual(["wizard", "mystic", "high"]);
    for (const table of [SKIN, HAIR, CLOTH, METAL, LEATHER, MAGE]) {
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

  it("gives the cape the party colour token, the only non-hex colour", () => {
    expect(CAPE_FILL).toBe("var(--sprite-fill)");
    expect(CAPE_SHADE).toContain("var(--sprite-fill)");
  });
});
