import { describe, expect, it } from "vitest";
import { ITEMS, cloakId, itemOf, type Item } from "../items";
import itemsSource from "../items.ts?raw";
import { SKIN } from "../palette";
import { ARMS, BEARDS, FEET, HAIR_STYLES, LEGS, TORSO, head } from "../parts";
import partsSource from "../parts.ts?raw";
import { SHAPES } from "../shapes";
import { type Face, type Frame, type SkinTone } from "../types";
import record from "../SOURCES.md?raw";

/**
 * The roster's drawings are named in one registry (shapes.ts), and appearance/SOURCES.md records how each was made
 * (#178). Here the registry is held to what the body kit and the equipment catalogue can draw, its names to the
 * constants that hold the drawings, and SOURCES.md's rows to the registry, so a shape cannot be drawn without a
 * provenance row, nor a row outlive or be renamed away from its shape.
 */

const FACES: Face[] = ["open", "closed", "smile", "frown"];
const HEAD_PIECES = ["HEAD_TOP", "EYES", "NOSE", "MOUTH", "HEAD_BOTTOM"];

/** Every frame inside ``value``: anything holding `rows` with at least one row, however deep (a weapon's rest and
 * raised frames, a layer, a drape, the shoulders, a frame a future field adds). */
function framesIn(value: unknown, out: Set<Frame> = new Set(), seen: Set<unknown> = new Set()): Set<Frame> {
  if (!value || typeof value !== "object" || seen.has(value)) return out;
  seen.add(value);
  if ("rows" in value) {
    if ((value as Frame).rows.length > 0) out.add(value as Frame);
    return out;
  }
  for (const inner of Object.values(value)) framesIn(inner, out, seen);
  return out;
}

/** Every item the catalogue can dress a character in: its own, and a cloak in each of its three shapes. */
function everyItem(): Item[] {
  const cloak = { base: "#336699", trim: "#ddbb44" } as const;
  const cloaks = [cloakId(cloak), cloakId(cloak, true), cloakId(cloak, false, true)].map((id) => itemOf(id));
  return [...(Object.values(ITEMS) as Item[]), ...cloaks.filter((item): item is Item => item !== undefined)];
}

/** Every frame the items and the body kit draw. */
function drawn(): Set<Frame> {
  return framesIn([everyItem(), HAIR_STYLES, BEARDS, ARMS, TORSO, LEGS, FEET]);
}

/** Each registered frame and the name it is registered under (a weapon's two frames under the weapon's name). */
function registered(): Map<Frame, string> {
  const out = new Map<Frame, string>();
  for (const [name, shape] of Object.entries(SHAPES)) {
    if (HEAD_PIECES.includes(name)) continue;
    for (const frame of framesIn(shape)) out.set(frame, name);
  }
  return out;
}

/** The text of a head piece (a row, a list of rows, rows by face), as rows. */
function rowsOf(piece: unknown): string[] {
  if (typeof piece === "string") return [piece];
  if (Array.isArray(piece)) return piece.flatMap(rowsOf);
  if (piece && typeof piece === "object") return Object.values(piece).flatMap(rowsOf);
  return [];
}

/** The names a registry block of ``source`` lists, one shorthand entry (`  NAME,`) a line. */
function shorthand(source: string, registry: string): string[] {
  const block = source.slice(source.indexOf(`export const ${registry}`));
  const body = block.slice(block.indexOf("{") + 1, block.indexOf("\n};"));
  return [...body.matchAll(/^ {2}([A-Z][A-Z0-9_]*),$/gm)].map((m) => m[1]);
}

describe("the roster's drawings", () => {
  it("are every one in the registry: every frame the items and the body kit draw", () => {
    const known = registered();
    const missing = [...drawn()].filter((frame) => !known.has(frame)).map((frame) => frame.rows[0]);
    expect(missing, "a drawing not in shapes.ts (and so with no SOURCES.md row)").toEqual([]);
  });

  it("are drawn, every one in the registry", () => {
    const used = drawn();
    const headRows = new Set(
      FACES.flatMap((face) => (Object.keys(SKIN) as SkinTone[]).flatMap((skin) => head(skin, face).frame.rows)),
    );
    const stale = Object.entries(SHAPES)
      .filter(([name, shape]) =>
        HEAD_PIECES.includes(name)
          ? !rowsOf(shape).every((row) => headRows.has(row))
          : [...framesIn(shape)].some((frame) => !used.has(frame)) || framesIn(shape).size === 0,
      )
      .map(([name]) => name);
    expect(stale, "a registered drawing nothing draws").toEqual([]);
  });

  it("draw the head only from its registered pieces", () => {
    const pieces = new Set(HEAD_PIECES.flatMap((name) => rowsOf(SHAPES[name])));
    const loose = FACES.flatMap((face) => head("light", face).frame.rows).filter((row) => !pieces.has(row));
    expect(loose, "a head row from no registered piece").toEqual([]);
  });

  it("are registered under the names of the constants that hold them", () => {
    // A name without a dot is a constant's, listed shorthand, so the key cannot say one name while the drawing is
    // another's; a dotted name is a record's entry, named from its key by code (parts.ts `named`).
    const listed = [...shorthand(partsSource, "BODY_SHAPES"), ...shorthand(itemsSource, "ITEM_SHAPES")];
    const plain = Object.keys(SHAPES).filter((name) => !name.includes("."));
    expect([...listed].sort(), "a registry entry not written as its constant's own name").toEqual([...plain].sort());
    const declared = (name: string) => new RegExp(`^(export )?const ${name}\\b`, "m");
    const undeclared = plain.filter((name) => !declared(name).test(partsSource) && !declared(name).test(itemsSource));
    expect(undeclared, "a registry name no constant has").toEqual([]);
  });

  it("have each one row in SOURCES.md, and SOURCES.md no other", () => {
    const rows = [...record.matchAll(/^\| `([A-Za-z_.]+)` \|/gm)].map((m) => m[1]);
    expect(new Set(rows).size, "a shape is listed twice").toBe(rows.length);
    expect([...rows].sort()).toEqual(Object.keys(SHAPES).sort());
  });
});
