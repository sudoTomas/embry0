import type { Region } from "@/api/world";

/**
 * The overworld's layout (spec 2026-10-25, Places): the guild hall at the
 * centre, each region on the next cell of a square spiral around it, the
 * busiest nearest, a lead at a spot its id picks inside its region. The same
 * regions always give the same map.
 */

export const CELL_W = 220;
export const CELL_H = 150;

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface PlacedRegion {
  region: Region;
  box: Box;
  /** The towns inside it, one per area. */
  towns: { name: string; x: number; y: number }[];
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

/** Every region in its place, the busiest nearest the hall; a region's size grows with its activity. */
export function layoutRegions(regions: readonly Region[]): PlacedRegion[] {
  const busiest = Math.max(1, ...regions.map((r) => r.jobs));
  const ordered = [...regions].sort((a, b) => b.jobs - a.jobs || a.repo.localeCompare(b.repo));
  return ordered.map((region, i) => {
    const { col, row } = spiralCell(i);
    const scale = 0.62 + 0.3 * Math.sqrt(region.jobs / busiest);
    const w = CELL_W * scale;
    const h = CELL_H * scale;
    const cx = col * CELL_W;
    const cy = row * CELL_H;
    const box = { x: cx - w / 2, y: cy - h / 2, w, h };
    const towns = region.areas.map((area, t) => {
      const spot = spotIn(box, `${region.repo}#${area.slug}`, 0.22);
      return { name: area.name, x: spot.x + (t % 2) * 2, y: spot.y };
    });
    return { region, box, towns };
  });
}

/** A stable point inside ``box`` for ``key``, ``margin`` of the box clear of its edges. */
export function spotIn(box: Box, key: string, margin = 0.15): { x: number; y: number } {
  const h = hash(key);
  const fx = (h & 0xffff) / 0xffff;
  const fy = (h >>> 16) / 0xffff;
  return {
    x: box.x + box.w * (margin + (1 - 2 * margin) * fx),
    y: box.y + box.h * (margin + (1 - 2 * margin) * fy),
  };
}

/** The bounding box of the whole map, the hall's cell included, with a border. */
export function mapBounds(placed: readonly PlacedRegion[], pad = 40): Box {
  const boxes = [{ x: -CELL_W / 3, y: -CELL_H / 3, w: (CELL_W * 2) / 3, h: (CELL_H * 2) / 3 }, ...placed.map((p) => p.box)];
  const x0 = Math.min(...boxes.map((b) => b.x)) - pad;
  const y0 = Math.min(...boxes.map((b) => b.y)) - pad;
  const x1 = Math.max(...boxes.map((b) => b.x + b.w)) + pad;
  const y1 = Math.max(...boxes.map((b) => b.y + b.h)) + pad;
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}
