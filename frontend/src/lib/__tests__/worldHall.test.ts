import { describe, expect, it } from "vitest";
import type { Loot } from "@/api/world";
import { MAX_ROWS, SPACING, STATION_OF, chestOf, hallLayout } from "../worldHall";

const loot = (merged: boolean): Loot => ({ job_id: "job-1", repo: "acme/web", area: null, title: "Add the export", pr_url: "https://example.test/pull/1", merged, at: null });

describe("the guild hall's inside", () => {
  it("walls the floor on every side, the top wall three tiles deep", () => {
    const { cols, rows, mask } = hallLayout(9, 5 * SPACING);
    const w = cols + 1;
    for (let c = 0; c <= cols; c++) {
      for (const r of [0, 1, 2, rows]) expect(mask[r * w + c]).toBe(1);
    }
    for (let r = 0; r <= rows; r++) expect(mask[r * w] + mask[r * w + cols]).toBe(2);
    expect(mask[4 * w + 3]).toBe(0);
  });

  it("puts every station on the floor, as many to a row as the room fits, none on another", () => {
    const layout = hallLayout(9, 400);
    expect(layout.perRow).toBe(4);
    expect(layout.width).toBeLessThanOrEqual(400);
    const spots = Array.from({ length: 9 }, (_, i) => layout.station(i));
    expect(new Set(spots.map((s) => `${s.x},${s.y}`)).size).toBe(9);
    expect(new Set(spots.map((s) => s.y)).size).toBe(3);
    for (const s of spots) {
      expect(s.x).toBeGreaterThanOrEqual(SPACING);
      expect(s.x + SPACING / 2).toBeLessThanOrEqual(layout.width);
      expect(s.y).toBeGreaterThan(3 * 32);
      expect(s.y).toBeLessThan(layout.height - 32);
    }
  });

  it("grows wider than a narrow room rather than taller than MAX_ROWS rows", () => {
    const layout = hallLayout(19, 340);
    expect(Math.ceil(19 / layout.perRow)).toBeLessThanOrEqual(MAX_ROWS);
    expect(layout.width).toBeGreaterThan(340);
  });

  it("gives the developer the anvil and the readers lecterns", () => {
    expect(STATION_OF.developer).toBe("anvil");
    expect(STATION_OF["pr-reviewer"]).toBe("lectern");
  });

  it("fills the chest by the loot: closed, open, gold once something merged", () => {
    expect(chestOf([])).toBe("chest_closed");
    expect(chestOf([loot(false)])).toBe("chest_open");
    expect(chestOf([loot(false), loot(true)])).toBe("chest_gold");
  });
});
