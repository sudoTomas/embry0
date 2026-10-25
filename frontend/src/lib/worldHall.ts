import type { Loot } from "@/api/world";

/**
 * The guild hall's inside (spec 2026-10-25, Guild hall): a floor walled on
 * every side, the top wall three tiles deep, and the stations in rows.
 */

/** Where each character works when it is home: the developer at the anvil, the readers at lecterns, and so on. */
export const STATION_OF: Record<string, string> = {
  developer: "anvil",
  review: "lectern",
  "pr-reviewer": "lectern",
  interrogator: "lectern",
  qa: "alchemy_table",
  kintsugi: "alchemy_table",
  analysis: "map_table",
  research: "map_table",
  ops: "map_table",
  "area-mapper": "map_table",
  "blast-radius": "map_table",
};

/** Pixels between two stations of a row, between two rows, and from the hall's top to the first row's feet. */
export const SPACING = 72;
const ROW = 96;
const TOP = 3 * 32 + 40;
/** The most rows of stations: past it the hall grows wider than its room and scrolls, so the cards below stay near. */
export const MAX_ROWS = 3;

/**
 * The hall's tiles and every station's place, for ``count`` stations in a
 * room ``room`` pixels wide: as many to a row as fit, never more than
 * MAX_ROWS rows, the rows centred.
 */
export function hallLayout(count: number, room: number, tile = 32) {
  const fits = Math.floor(room / tile);
  const perRow = Math.max(3, Math.floor((fits * tile) / SPACING) - 1, Math.ceil(count / MAX_ROWS));
  const stationRows = Math.max(1, Math.ceil(count / perRow));
  const cols = Math.max(Math.ceil(((perRow + 1) * SPACING) / tile), fits);
  const rows = Math.ceil((TOP + (stationRows - 1) * ROW + 56) / tile) + 1;
  const mask = new Uint8Array((cols + 1) * (rows + 1));
  for (let r = 0; r <= rows; r++)
    for (let c = 0; c <= cols; c++) if (r <= 2 || r === rows || c === 0 || c === cols) mask[r * (cols + 1) + c] = 1;
  const inset = Math.floor((cols * tile - (perRow - 1) * SPACING) / 2);
  const station = (i: number) => ({ x: inset + (i % perRow) * SPACING, y: TOP + Math.floor(i / perRow) * ROW });
  return { cols, rows, perRow, mask, width: cols * tile, height: rows * tile, station };
}

/** The chest's look: gold once something merged this week, open when a pull request came back, closed otherwise. */
export function chestOf(loot: readonly Loot[]): "chest_gold" | "chest_open" | "chest_closed" {
  if (loot.some((l) => l.merged)) return "chest_gold";
  return loot.length ? "chest_open" : "chest_closed";
}
