import { describe, expect, it } from "vitest";
import type { Region } from "@/api/world";
import { CELL_COLS, CELL_ROWS, MAX_REGIONS, TERRAINS, buildTileMap, cornerIndex, hash, leadSpot, spiralCell, tileIndex, type TileMap } from "../worldTiles";

const region = (repo: string, jobs: number, areas: string[] = []): Region => ({
  repo,
  jobs,
  last_job_at: null,
  owned: false,
  watched: false,
  areas: areas.map((name) => ({ name, slug: name.toLowerCase() })),
});

const REGIONS = [region("acme/a", 1), region("acme/b", 50, ["Billing", "Search"]), region("acme/c", 5), region("acme/d", 9)];

const at = (map: TileMap, terrain: (typeof TERRAINS)[number], c: number, r: number) => map.masks[terrain][r * (map.cols + 1) + c];

/** The corners joined to the hall's door along dirt and cobbles. */
function reachable(map: TileMap): Set<number> {
  const w = map.cols + 1;
  const road = (c: number, r: number) => c >= 0 && r >= 0 && c <= map.cols && r <= map.rows && (at(map, "dirt", c, r) || at(map, "cobbles", c, r));
  const start = { c: Math.round(map.hall.x / map.tile), r: Math.round((map.hall.y - 8) / map.tile) };
  const seen = new Set([start.r * w + start.c]);
  const queue = [start];
  while (queue.length) {
    const { c, r } = queue.pop()!;
    for (const [dc, dr] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) {
      const n = { c: c + dc, r: r + dr };
      if (road(n.c, n.r) && !seen.has(n.r * w + n.c)) {
        seen.add(n.r * w + n.c);
        queue.push(n);
      }
    }
  }
  return seen;
}

describe("the overworld's tile map", () => {
  it("picks a tile from its four corners, NW the high bit", () => {
    expect(cornerIndex(1, 0, 0, 0)).toBe(8);
    expect(cornerIndex(0, 1, 0, 0)).toBe(4);
    expect(cornerIndex(0, 0, 1, 0)).toBe(2);
    expect(cornerIndex(0, 0, 0, 1)).toBe(1);
    expect(cornerIndex(1, 1, 1, 1)).toBe(15);
    const mask = new Uint8Array([1, 0, 0, 1]);
    expect(tileIndex(mask, 1, 0, 0)).toBe(9);
  });

  it("walks a square spiral, and hashes stably", () => {
    expect([0, 1, 2, 3, 4].map(spiralCell)).toEqual([
      { col: 1, row: 0 },
      { col: 1, row: 1 },
      { col: 0, row: 1 },
      { col: -1, row: 1 },
      { col: -1, row: 0 },
    ]);
    expect(hash("a")).toBe(hash("a"));
    expect(hash("a")).not.toBe(hash("b"));
  });

  it("is the same map for the same regions, in any order, the busiest nearest the hall", () => {
    const one = buildTileMap(REGIONS);
    const two = buildTileMap([...REGIONS].reverse());
    expect(two.objects).toEqual(one.objects);
    for (const t of TERRAINS) expect(two.masks[t]).toEqual(one.masks[t]);
    expect(one.regions[0].repo).toBe("acme/b");
    expect(one.regions[0].towns.map((t) => t.name)).toEqual(["Billing", "Search"]);
    const size = (p: TileMap["regions"][number]["plaza"]) => (p.c1 - p.c0) * (p.r1 - p.r0);
    expect(size(one.regions[0].plaza)).toBeGreaterThan(size(one.regions.at(-1)!.plaza));
    expect(one.objects.filter((o) => o.name === "guild_hall")).toHaveLength(1);
    expect(one.objects.filter((o) => o.name === "signpost")).toHaveLength(REGIONS.length);
  });

  it("joins the hall's door to every square by road, and grows nothing wild on a road", () => {
    const map = buildTileMap(REGIONS);
    const roads = reachable(map);
    const w = map.cols + 1;
    for (const r of map.regions) {
      const c = Math.floor((r.plaza.c0 + r.plaza.c1) / 2);
      const row = Math.floor((r.plaza.r0 + r.plaza.r1) / 2);
      expect(roads.has(row * w + c), r.repo).toBe(true);
    }
    for (let i = 0; i < map.masks.dirt.length; i++)
      if (map.masks.dirt[i] || map.masks.cobbles[i]) for (const t of ["water", "forest", "rock"] as const) expect(map.masks[t][i]).toBe(0);
  });

  it("grows forest, rock or water on the cells no region takes", () => {
    const map = buildTileMap([region("acme/a", 1)]);
    expect(map.cols).toBe(3 * CELL_COLS);
    expect(map.rows).toBe(3 * CELL_ROWS);
    const wild = ["water", "forest", "rock"] as const;
    expect(wild.some((t) => map.masks[t].some((v) => v === 1))).toBe(true);
  });

  it("draws at most MAX_REGIONS regions", () => {
    const many = Array.from({ length: MAX_REGIONS + 6 }, (_, i) => region(`acme/r${i}`, i));
    expect(buildTileMap(many).regions).toHaveLength(MAX_REGIONS);
  });

  it("stands a lead on grass inside its region's cell, the same spot every time", () => {
    const map = buildTileMap(REGIONS);
    const cell = map.regions.find((r) => r.repo === "acme/c")!.cell;
    for (const key of ["case:1", "case:2", "finding:9"]) {
      const spot = leadSpot(map, "acme/c", key);
      expect(leadSpot(map, "acme/c", key)).toEqual(spot);
      expect(spot.x).toBeGreaterThanOrEqual(cell.c0 * map.tile);
      expect(spot.x).toBeLessThan(cell.c1 * map.tile);
      expect(spot.y).toBeGreaterThan(cell.r0 * map.tile);
      expect(spot.y).toBeLessThanOrEqual(cell.r1 * map.tile);
    }
    expect(leadSpot(map, "acme/missing", "case:1")).toEqual({ x: map.hall.x + 48, y: map.hall.y + 32 });
  });
});
