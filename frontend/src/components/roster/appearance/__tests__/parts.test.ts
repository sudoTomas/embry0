import { describe, expect, it } from "vitest";
import { GRID, type ArmPose, type BeardStyle, type Face, type HairStyle, type SkinTone } from "../types";
import { ARMS, BEARDS, FEET, HAIR_STYLES, LEGS, TORSO, arm, beard, feet, hair, head, legs, mirror, torso } from "../parts";

const HAIRS: HairStyle[] = ["short", "spiky", "long", "mohawk", "bald", "curly", "fringe"];
const BEARD_STYLES: BeardStyle[] = ["goatee", "full", "moustache", "long"];
const SKINS: SkinTone[] = ["light", "tan", "brown", "dark"];
const POSES: ArmPose[] = ["down", "up", "slump"];

function cells(rows: readonly string[]): string[] {
  return [...new Set(rows.join("").replace(/\./g, ""))];
}

describe("body-kit parts", () => {
  it("draws every hair style and beard as a rectangular frame inside the grid", () => {
    for (const style of HAIRS) {
      const f = HAIR_STYLES[style];
      for (const row of f.rows) expect(row, style).toHaveLength(f.rows[0].length);
      expect(f.x + (f.rows[0]?.length ?? 0), style).toBeLessThanOrEqual(GRID);
      expect(f.y + f.rows.length, style).toBeLessThanOrEqual(GRID);
      if (f.rows.length) expect(f.y, `${style} leaves row 0 free`).toBeGreaterThanOrEqual(1);
    }
    expect(HAIR_STYLES.bald.rows).toEqual([]);
    for (const style of BEARD_STYLES) {
      const f = BEARDS[style];
      expect(f.rows.length, style).toBeGreaterThan(0);
      for (const row of f.rows) expect(row, style).toHaveLength(f.rows[0].length);
    }
  });

  it("resolves every character of every part against its colour key", () => {
    const layers = [
      ...SKINS.flatMap((s) => (["open", "closed", "smile", "frown"] as Face[]).map((f) => head(s, f))),
      ...HAIRS.map((h) => hair(h, "brown")),
      ...BEARD_STYLES.map((b) => beard(b, "black")),
      torso("red"), legs("blue"), feet("umber"),
      ...POSES.flatMap((p) => [arm("left", p, "#112233", "#f0c8a0"), arm("right", p, "#112233", "#f0c8a0")]),
    ];
    for (const layer of layers) {
      for (const c of cells(layer.frame.rows)) expect(layer.colours[c], `${c} in ${JSON.stringify(layer.frame.rows)}`).toBeTruthy();
    }
  });

  it("opens, closes and shapes the face: eyes only when open or smiling, mouth cells moved for the frown", () => {
    const eyes = (f: Face) => head("light", f).frame.rows.join("").split("e").length - 1;
    expect(eyes("open")).toBe(2);
    expect(eyes("smile")).toBe(2);
    expect(eyes("closed")).toBe(0);
    expect(eyes("frown")).toBe(2);
    expect(head("light", "frown").frame.rows).not.toEqual(head("light", "open").frame.rows);
    expect(head("light", "smile").frame.rows).not.toEqual(head("light", "open").frame.rows);
  });

  it("stands on the last row with the torso above the legs above the feet", () => {
    expect(FEET.y + FEET.rows.length).toBe(GRID);
    expect(LEGS.y + LEGS.rows.length).toBeLessThanOrEqual(FEET.y + 1);
    expect(TORSO.y + TORSO.rows.length).toBeLessThanOrEqual(LEGS.y + 1);
  });

  it("raises the up arm above the down arm and keeps the right arm a mirror of the left", () => {
    expect(ARMS.up.y).toBeLessThan(ARMS.down.y);
    const left = arm("left", "down", "#112233", "#f0c8a0").frame;
    const right = arm("right", "down", "#112233", "#f0c8a0").frame;
    expect(right).toEqual(mirror(left));
    expect(right.x).toBeGreaterThan(left.x);
    expect(mirror({ x: 2, y: 5, rows: ["ab", "c."] })).toEqual({ x: GRID - 4, y: 5, rows: ["ba", ".c"] });
  });

  it("colours the sleeves from the torso slot and the hands from the skin unless told otherwise", () => {
    const a = arm("left", "down", "#112233", "#f0c8a0");
    expect(a.colours.a).toBe("#112233");
    expect(a.colours.g).toBe("#f0c8a0");
  });
});
