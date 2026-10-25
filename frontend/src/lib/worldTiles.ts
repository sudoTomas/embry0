import type { Region } from "@/api/world";

/**
 * The overworld as a tile map (spec 2026-10-25, Places; public/world/README.md):
 * grass everywhere, the guild hall in the centre cell, each region a cobbled
 * town square on the next cell of a spiral (the busiest nearest), dirt paths
 * from the hall's door to every square, and the cells no region takes grown
 * over with forest, rock or a pond. Terrain is a grid of corners, one mask per
 * terrain; a corner tileset picks each tile from its four corners. Scenery
 * objects stand where their spot is grass. The same regions always give the
 * same map.
 */

export const TERRAINS = ["water", "forest", "rock", "dirt", "cobbles"] as const;
export type Terrain = (typeof TERRAINS)[number];

/** A map's size in tiles of one cell (a region, or the hall's). */
export const CELL_COLS = 14;
export const CELL_ROWS = 10;
/** The most regions the map draws; the rest of the organization stays on the frontier list. */
export const MAX_REGIONS = 24;
/** Houses a town square shows at most, one per area. */
export const MAX_TOWNS = 6;

export interface PlacedObject {
  name: string;
  /** The map pixel the object's anchor stands on. */
  x: number;
  y: number;
}

export interface MapRegion {
  repo: string;
  region: Region;
  /** The square, in tiles: columns c0..c1-1, rows r0..r1-1 are cobbles. */
  plaza: { c0: number; r0: number; c1: number; r1: number };
  /** The cell, in tiles. */
  cell: { c0: number; r0: number; c1: number; r1: number };
  /** Where its name is written, and where a party with no lead to fight stands. */
  label: { x: number; y: number };
  centre: { x: number; y: number };
  towns: { name: string; x: number; y: number }[];
}

export interface TileMap {
  cols: number;
  rows: number;
  tile: number;
  masks: Record<Terrain, Uint8Array>;
  objects: PlacedObject[];
  regions: MapRegion[];
  /** The guild hall's anchor (the bottom of its door). */
  hall: { x: number; y: number };
}

/** The tile chosen from its four corners: 8·NW + 4·NE + 2·SW + 1·SE (public/world/README.md). */
export function cornerIndex(nw: number, ne: number, sw: number, se: number): number {
  return 8 * nw + 4 * ne + 2 * sw + se;
}

/** A tile's index in a tileset whose terrain ``on`` is the corner value 1: tile 0 holds none of it. */
export function tileIndex(mask: Uint8Array, cols: number, c: number, r: number): number {
  const w = cols + 1;
  return cornerIndex(mask[r * w + c], mask[r * w + c + 1], mask[(r + 1) * w + c], mask[(r + 1) * w + c + 1]);
}

/** FNV-1a, 32 bits: a stable number from a string. */
export function hash(text: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i++) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h >>> 0;
}

/** The ``n``-th cell of a square spiral out from (0, 0), the centre excluded: n = 0 is (1, 0). */
export function spiralCell(n: number): { col: number; row: number } {
  let col = 0;
  let row = 0;
  let leg = 1;
  let dir = 0;
  let step = 0;
  const moves = [
    [1, 0],
    [0, 1],
    [-1, 0],
    [0, -1],
  ];
  for (let i = 0; i <= n; i++) {
    col += moves[dir][0];
    row += moves[dir][1];
    step += 1;
    if (step === leg) {
      step = 0;
      dir = (dir + 1) % 4;
      if (dir % 2 === 0) leg += 1;
    }
  }
  return { col, row };
}

function blankMasks(cols: number, rows: number): Record<Terrain, Uint8Array> {
  const size = (cols + 1) * (rows + 1);
  return Object.fromEntries(TERRAINS.map((t) => [t, new Uint8Array(size)])) as Record<Terrain, Uint8Array>;
}

/** Fix a few hashes per key: a cheap stream of numbers in [0, 1). */
function rng(key: string): () => number {
  let state = hash(key) || 1;
  return () => {
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    state >>>= 0;
    return state / 0x100000000;
  };
}

export function buildTileMap(regions: readonly Region[], tile = 32): TileMap {
  const ordered = [...regions].sort((a, b) => b.jobs - a.jobs || a.repo.localeCompare(b.repo)).slice(0, MAX_REGIONS);
  let ring = 1;
  while ((2 * ring + 1) ** 2 - 1 < ordered.length) ring += 1;
  const side = 2 * ring + 1;
  const cols = side * CELL_COLS;
  const rows = side * CELL_ROWS;
  const masks = blankMasks(cols, rows);
  const w = cols + 1;
  const set = (t: Terrain, c: number, r: number, v = 1) => {
    if (c >= 0 && r >= 0 && c <= cols && r <= rows) masks[t][r * w + c] = v;
  };
  const get = (t: Terrain, c: number, r: number) => (c >= 0 && r >= 0 && c <= cols && r <= rows ? masks[t][r * w + c] : 0);
  const cellOrigin = (col: number, row: number) => ({ c0: (col + ring) * CELL_COLS, r0: (row + ring) * CELL_ROWS });

  // The hall: its building in the centre cell, a small square at its door.
  const hc = cellOrigin(0, 0);
  const door = { c: hc.c0 + Math.floor(CELL_COLS / 2), r: hc.r0 + Math.floor(CELL_ROWS / 2) + 1 };
  for (let c = door.c - 3; c <= door.c + 3; c++) for (let r = door.r; r <= door.r + 3; r++) set("cobbles", c, r);
  const hall = { x: door.c * tile, y: door.r * tile + 8 };
  const objects: PlacedObject[] = [];

  // The regions, their squares, their towns, and a path from the hall to each.
  const busiest = Math.max(1, ...ordered.map((r) => r.jobs));
  const placed: MapRegion[] = ordered.map((region, i) => {
    const { col, row } = spiralCell(i);
    const cell = cellOrigin(col, row);
    const rand = rng(region.repo);
    const reach = Math.sqrt(region.jobs / busiest);
    const pw = 5 + Math.round(3 * reach);
    const ph = 3 + Math.round(2 * reach);
    const c0 = cell.c0 + Math.floor((CELL_COLS - pw) / 2) + Math.round(rand() * 2 - 1);
    const r0 = cell.r0 + Math.floor((CELL_ROWS - ph) / 2) + Math.round(rand() * 2 - 1);
    const plaza = { c0, r0, c1: c0 + pw, r1: r0 + ph };
    for (let c = plaza.c0; c <= plaza.c1; c++) for (let r = plaza.r0; r <= plaza.r1; r++) set("cobbles", c, r);
    const towns = region.areas.slice(0, MAX_TOWNS).map((area, t) => {
      const x = (plaza.c0 + 1 + ((t * 2) % Math.max(2, pw - 1))) * tile + tile / 2;
      const y = (plaza.r0 + 2 + Math.floor((t * 2) / Math.max(2, pw - 1)) * 2) * tile;
      objects.push({ name: "house", x, y });
      return { name: area.name, x, y };
    });
    if (towns.length === 0) {
      objects.push({ name: "house", x: (plaza.c0 + 1) * tile + tile / 2, y: (plaza.r0 + 2) * tile });
      objects.push({ name: "well", x: (plaza.c1 - 1) * tile, y: (plaza.r1 - 1) * tile });
    }
    objects.push({ name: "banner", x: plaza.c1 * tile - 6, y: (plaza.r0 + 1) * tile });
    // The path: along the hall's row, then up or down to the square's middle (or the other way round).
    const pc = Math.floor((plaza.c0 + plaza.c1) / 2);
    const pr = Math.floor((plaza.r0 + plaza.r1) / 2);
    const horizontalFirst = rand() < 0.5;
    const corner = horizontalFirst ? { c: pc, r: door.r + 1 } : { c: door.c, r: pr };
    // One corner wide: the tiles either side of the line each draw half of it, a path a tile wide.
    const stroke = (a: { c: number; r: number }, b: { c: number; r: number }) => {
      for (let c = Math.min(a.c, b.c); c <= Math.max(a.c, b.c); c++)
        for (let r = Math.min(a.r, b.r); r <= Math.max(a.r, b.r); r++) set("dirt", c, r);
    };
    stroke({ c: door.c, r: door.r + 1 }, corner);
    stroke(corner, { c: pc, r: pr });
    return {
      repo: region.repo,
      region,
      plaza,
      cell: { c0: cell.c0, r0: cell.r0, c1: cell.c0 + CELL_COLS, r1: cell.r0 + CELL_ROWS },
      label: { x: ((plaza.c0 + plaza.c1) / 2) * tile, y: plaza.r0 * tile - 4 },
      centre: { x: pc * tile, y: (pr + 1) * tile },
      towns,
    };
  });

  // The cells no region takes: forest, rock or a pond, by the cell's own hash.
  const taken = new Set(["0,0", ...ordered.map((_, i) => `${spiralCell(i).col},${spiralCell(i).row}`)]);
  for (let col = -ring; col <= ring; col++) {
    for (let row = -ring; row <= ring; row++) {
      if (taken.has(`${col},${row}`)) continue;
      const cell = cellOrigin(col, row);
      const rand = rng(`wild ${col},${row}`);
      const kind: Terrain = (["forest", "forest", "rock", "water"] as const)[Math.floor(rand() * 4)];
      const rx = (CELL_COLS / 2) * (0.55 + rand() * 0.3);
      const ry = (CELL_ROWS / 2) * (0.55 + rand() * 0.3);
      const cx = cell.c0 + CELL_COLS / 2;
      const cy = cell.r0 + CELL_ROWS / 2;
      for (let c = cell.c0; c <= cell.c0 + CELL_COLS; c++)
        for (let r = cell.r0; r <= cell.r0 + CELL_ROWS; r++) if (((c - cx) / rx) ** 2 + ((r - cy) / ry) ** 2 <= 1) set(kind, c, r);
      if (kind === "forest")
        for (let k = 0; k < 4; k++)
          objects.push({ name: rand() < 0.7 ? "tree_pine" : "tree_oak", x: (cx + (rand() - 0.5) * rx) * tile, y: (cy + (rand() - 0.5) * ry) * tile });
      if (kind === "rock") objects.push({ name: "boulder", x: cx * tile, y: cy * tile });
    }
  }

  // Paths and squares win: the wild keeps a corner clear around them, and no path runs under a square.
  for (let c = 0; c <= cols; c++)
    for (let r = 0; r <= rows; r++) {
      let near = false;
      for (let dc = -1; dc <= 1 && !near; dc++) for (let dr = -1; dr <= 1 && !near; dr++) near = !!(get("dirt", c + dc, r + dr) || get("cobbles", c + dc, r + dr));
      if (near) for (const t of ["water", "forest", "rock"] as const) set(t, c, r, 0);
      if (get("cobbles", c, r) && get("cobbles", c - 1, r) && get("cobbles", c + 1, r) && get("cobbles", c, r - 1) && get("cobbles", c, r + 1)) set("dirt", c, r, 0);
    }

  // Scenery on the grass of every region's cell: trees, bushes, flowers, a fence, a signpost.
  const grassAt = (c: number, r: number) =>
    TERRAINS.every((t) => !get(t, c, r) && !get(t, c + 1, r) && !get(t, c, r + 1) && !get(t, c + 1, r + 1));
  for (const p of placed) {
    const rand = rng(`scenery ${p.repo}`);
    const kinds = ["tree_oak", "tree_pine", "bush", "flowers", "rock_small", "tree_oak", "fence", "bush"];
    let placedCount = 0;
    for (let tries = 0; tries < 40 && placedCount < 6; tries++) {
      const c = p.cell.c0 + 1 + Math.floor(rand() * (CELL_COLS - 2));
      const r = p.cell.r0 + 1 + Math.floor(rand() * (CELL_ROWS - 2));
      if (!grassAt(c, r) || (c >= p.plaza.c0 - 1 && c <= p.plaza.c1 && r >= p.plaza.r0 - 2 && r <= p.plaza.r1)) continue;
      objects.push({ name: kinds[placedCount % kinds.length], x: c * tile + tile / 2, y: (r + 1) * tile });
      placedCount += 1;
    }
    objects.push({ name: "signpost", x: (p.plaza.c0 - 1) * tile, y: (p.plaza.r1 + 1) * tile });
  }
  objects.push({ name: "guild_hall", x: hall.x, y: hall.y });

  return { cols, rows, tile, masks, objects: objects.sort((a, b) => a.y - b.y), regions: placed, hall };
}

/**
 * Where a lead stands in its region: a grass spot of the region's cell its id
 * picks, clear of the square and the paths; the square's edge when none is.
 */
export function leadSpot(map: TileMap, repo: string, key: string): { x: number; y: number } {
  const region = map.regions.find((r) => r.repo === repo);
  if (!region) return { x: map.hall.x + 48, y: map.hall.y + 32 };
  const rand = rng(`lead ${key}`);
  const w = map.cols + 1;
  const clear = (c: number, r: number) =>
    TERRAINS.every((t) => [0, 1].every((dc) => [0, 1].every((dr) => !map.masks[t][(r + dr) * w + c + dc])));
  for (let tries = 0; tries < 30; tries++) {
    const c = region.cell.c0 + 1 + Math.floor(rand() * (CELL_COLS - 2));
    const r = region.cell.r0 + 1 + Math.floor(rand() * (CELL_ROWS - 2));
    if (clear(c, r)) return { x: c * map.tile + map.tile / 2, y: (r + 1) * map.tile };
  }
  return { x: (region.plaza.c1 + 1) * map.tile, y: region.plaza.r1 * map.tile };
}
