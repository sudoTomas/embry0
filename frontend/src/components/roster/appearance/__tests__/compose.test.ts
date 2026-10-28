import { describe, expect, it } from "vitest";
import { GHOST_FILL, POSES, SPARK, SWEAT, compose, layersOf, paint, runLength, walkFrames, type NamedLayer, type Pose } from "../compose";
import { ITEMS } from "../items";
import { COLOURS, OUTLINE } from "../palette";
import { GRID, type Appearance } from "../types";

const bare: Appearance = {
  skin: "light", hair: "long", beard: "full",
  colours: { hair: "auburn", torso: "cream", legs: "umber", feet: "brown" },
  equipment: {},
};
const armed: Appearance = { ...bare, equipment: { head: "obsidian-helm", shield: "obsidian-heater-shield", weapon: "obsidian-sabre", body: "obsidian-chestplate" } };
const idle: Pose = POSES.idle[0];

function colours(a: Appearance, pose: Pose): Set<string> {
  return new Set(paint(a, pose).flat().filter((c): c is string => c !== null));
}

function box(layer: NamedLayer): { top: number; bottom: number } {
  const rows = layer.frame.rows.map((r, j) => [r, j] as const).filter(([r]) => /[^.]/.test(r)).map(([, j]) => j);
  return { top: layer.frame.y + Math.min(...rows), bottom: layer.frame.y + Math.max(...rows) };
}

describe("compose", () => {
  it("lays the layers in draw order: cape first, weapon last, head slot after hair and beard, shield after the body", () => {
    const names = layersOf({ ...armed, equipment: { ...armed.equipment, cape: "cape", neck: "amulet" } }, idle).map((l) => l.name);
    expect(names[0]).toBe("cape");
    expect(names.at(-1)).toBe("weapon");
    expect(names.indexOf("shield")).toBeGreaterThan(names.lastIndexOf("body"));
    expect(names.indexOf("head-item")).toBeGreaterThan(names.indexOf("head"));
    expect(names.indexOf("neck")).toBeGreaterThan(names.indexOf("head-item"));
    expect(names.indexOf("shoulders")).toBeGreaterThan(names.indexOf("arm-right"));
  });

  it("hides the hair and beard under a full helm, the hair alone under a hood, neither under a hat", () => {
    const hair = COLOURS.auburn;
    expect(colours(bare, idle).has(hair), "bare head shows the hair").toBe(true);
    expect(colours(armed, idle).has(hair), "a full helm hides hair and beard").toBe(false);
    expect(layersOf(armed, idle).map((l) => l.name)).not.toContain("beard");
    const hooded = layersOf({ ...bare, equipment: { head: "green-hood" } }, idle).map((l) => l.name);
    expect(hooded).not.toContain("hair");
    expect(hooded).toContain("beard");
    const hatted = layersOf({ ...bare, equipment: { head: "novice-hat" } }, idle).map((l) => l.name);
    expect(hatted).toContain("hair");
    expect(hatted).toContain("beard");
  });

  it("draws the shield over the body: the grid there holds the shield's colours, not the plate's", () => {
    const grid = paint(armed, idle);
    const shield = ITEMS["obsidian-heater-shield"];
    const plate = ITEMS["obsidian-chestplate"];
    const f = shield.layer!;
    let covered = 0;
    f.rows.forEach((row, j) => [...row].forEach((c, i) => {
      if (c === ".") return;
      expect(grid[f.y + j][f.x + i]).toBe(shield.colours[c]);
      covered++;
    }));
    expect(covered).toBeGreaterThan(40);
    const under = paint({ ...armed, equipment: { ...armed.equipment, shield: undefined } }, idle);
    expect(under[14][22], "without the shield the arm shows there").not.toBe(shield.colours["2"]);
    expect(plate.layer!.x + plate.layer!.rows[0].length, "the shield overlaps the plate's edge").toBeGreaterThan(f.x);
  });

  it("raises the weapon in the tool pose: its layer's box moves up; the second tool frame is the rest", () => {
    const rest = box(layersOf(armed, POSES.idle[0]).find((l) => l.name === "weapon")!);
    const raised = box(layersOf(armed, POSES.tool[0]).find((l) => l.name === "weapon")!);
    expect(raised.top).toBeLessThan(rest.top);
    expect(raised.bottom).toBeLessThan(rest.bottom);
    expect(POSES.tool[1]).toEqual(POSES.idle[0]);
    expect(POSES.tool[0].arms).toEqual({ left: "up", right: "down" });
  });

  it("raises both arms, the weapon and the shield in the done pose, with a smile", () => {
    const down = layersOf(armed, POSES.idle[0]);
    const up = layersOf(armed, POSES.done[0]);
    for (const name of ["arm-left", "arm-right", "shield", "weapon"] as const) {
      expect(box(up.find((l) => l.name === name)!).top, name).toBeLessThan(box(down.find((l) => l.name === name)!).top);
    }
    expect(POSES.done[0].face).toBe("smile");
  });

  it("slumps the arms, frowns and sweats in the failed pose", () => {
    const pose = POSES.failed[0];
    expect(pose.arms).toEqual({ left: "slump", right: "slump" });
    expect(pose.face).toBe("frown");
    const frown = layersOf(bare, pose).find((l) => l.name === "head")!.frame.rows;
    const open = layersOf(bare, idle).find((l) => l.name === "head")!.frame.rows;
    expect(frown).not.toEqual(open);
    expect(colours(bare, pose).has(SWEAT)).toBe(true);
    expect(colours(bare, idle).has(SWEAT)).toBe(false);
  });

  it("closes the eyes and sparks row 0 on thinking frame 0, opens them and moves the spark on frame 1", () => {
    const [t0, t1] = POSES.thinking;
    expect([t0.face, t1.face]).toEqual(["closed", "open"]);
    const g0 = paint(bare, t0);
    const g1 = paint(bare, t1);
    expect(g0[0].indexOf(SPARK)).toBeGreaterThan(0);
    expect(g1[0].indexOf(SPARK)).toBe(g0[0].indexOf(SPARK) + 1);
    expect(g0.flat().filter((c) => c === "#1a1420")).toHaveLength(0);
    expect(g1.flat().filter((c) => c === "#1a1420")).toHaveLength(2);
  });

  it("bobs idle frame 1 up one row, leaving row 0 free in frame 0", () => {
    const [a, b] = POSES.idle.map((p) => paint(bare, p));
    expect(a[0].every((c) => c === null)).toBe(true);
    expect(b.slice(0, GRID - 1)).toEqual(a.slice(1));
  });

  it("draws pending as the silhouette's edge in the dim tint alone", () => {
    const frame = compose(armed, POSES.pending[0]);
    expect(Object.keys(frame.paths)).toEqual([GHOST_FILL]);
    const ghost = paint(armed, POSES.pending[0]);
    const solid = paint(armed, idle);
    for (let y = 0; y < GRID; y++) for (let x = 0; x < GRID; x++) {
      if (ghost[y][x] !== null) expect(solid[y][x], `${x},${y} is outside the figure`).not.toBeNull();
    }
    expect(ghost[20][15], "an interior cell is empty").toBeNull();
  });

  it("erases lower layers under an x cell", () => {
    const grid = paint({ ...bare, hair: "spiky", equipment: { head: "novice-hat" } }, idle);
    expect(grid[1][12], "the spike under the crown is gone").toBeNull();
  });

  it("refuses a layer character its colour key cannot resolve", () => {
    const broken = { ...bare, hair: "short" as const };
    expect(() => paint(broken, idle)).not.toThrow();
    expect(() => runLength([["#000000", "#000000", null], [null, "#ffffff", "#ffffff"]])).not.toThrow();
  });

  it("run-length encodes a grid into one rectangle path per colour", () => {
    expect(runLength([["o", null, "f", "f"]])).toEqual({ o: "M0 0h1v1h-1z", f: "M2 0h2v1h-2z" });
    expect(runLength([["a", "a", null], ["a", "a", "b"], ["a", null, "b"]]), "runs repeated below merge into one rectangle").toEqual({
      a: "M0 0h2v2h-2zM0 2h1v1h-1z",
      b: "M2 1h1v2h-1z",
    });
    expect(compose(bare, idle).size).toBe(GRID);
    expect(compose(bare, idle).paths[OUTLINE]).toMatch(/^M\d+ \d+h\d+v1h-\d+z/);
  });
});

describe("walkFrames", () => {
  it("steps in two frames, one leg lifted in each, the same size as a pose", () => {
    const [left, right] = walkFrames(armed);
    const still = compose(armed, idle);
    expect(left.size).toBe(GRID);
    expect(right.size).toBe(GRID);
    expect(left.paths).not.toEqual(right.paths);
    expect(left.paths).not.toEqual(still.paths);
    expect(walkFrames(armed)).toEqual([left, right]);
  });
});
