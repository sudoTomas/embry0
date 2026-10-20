import { describe, expect, it } from "vitest";
import { ITEMS, type Item } from "../items";
import { CAPE_FILL } from "../palette";
import { GRID, type EquipmentSlot, type Frame } from "../types";

const SLOTS: EquipmentSlot[] = ["head", "cape", "neck", "weapon", "body", "shield", "legs", "hands", "feet"];
const items = Object.entries(ITEMS) as [string, Item][];

function framesOf(item: Item): [string, Frame][] {
  const out: [string, Frame][] = [];
  if (item.layer) out.push(["layer", item.layer]);
  if (item.shoulders) out.push(["shoulders", item.shoulders]);
  if (item.weapon) out.push(["rest", item.weapon.rest], ["raised", item.weapon.raised]);
  return out;
}

describe("equipment catalogue", () => {
  it("keys every item by its id in a known slot, drawn or recolouring", () => {
    for (const [key, item] of items) {
      expect(item.id, key).toBe(key);
      expect(SLOTS, key).toContain(item.slot);
      const drawn = framesOf(item).length > 0;
      expect(drawn || item.hands !== undefined, `${key} draws nothing and recolours nothing`).toBe(true);
      if (item.slot === "weapon") expect(item.weapon, `${key} needs an at-rest and a raised layer`).toBeDefined();
      else expect(item.weapon, `${key} is not a weapon`).toBeUndefined();
    }
  });

  it("draws rectangular frames inside the grid whose every cell resolves in the item's colour key", () => {
    for (const [key, item] of items) {
      for (const [name, f] of framesOf(item)) {
        const width = f.rows[0]?.length ?? 0;
        expect(width, `${key}.${name} is empty`).toBeGreaterThan(0);
        for (const row of f.rows) expect(row, `${key}.${name} ragged`).toHaveLength(width);
        expect(f.x, `${key}.${name} x`).toBeGreaterThanOrEqual(0);
        expect(f.y, `${key}.${name} y`).toBeGreaterThanOrEqual(0);
        expect(f.x + width, `${key}.${name} right edge`).toBeLessThanOrEqual(GRID);
        expect(f.y + f.rows.length, `${key}.${name} bottom edge`).toBeLessThanOrEqual(GRID);
        for (const c of new Set(f.rows.join("").replace(/[.x]/g, ""))) {
          expect(item.colours[c], `${key}.${name} has no colour for ${JSON.stringify(c)}`).toBeTruthy();
        }
      }
    }
  });

  it("puts weapons on the viewer's left and the shield on the right, with the raised weapon above the resting one", () => {
    for (const [key, item] of items) {
      if (item.weapon) {
        expect(item.weapon.rest.x, `${key} rests in the left hand`).toBeLessThan(GRID / 2);
        expect(item.weapon.raised.y, `${key} raised above rest`).toBeLessThan(item.weapon.rest.y);
      }
      if (item.slot === "shield") expect(item.layer!.x, `${key} on the right arm`).toBeGreaterThanOrEqual(GRID / 2);
    }
  });

  it("covers the hair under every helm, hood and hat that says so, and the beard only under a full helm", () => {
    for (const [key, item] of items) {
      if (item.over) expect(item.slot, `${key} over= is for the head slot`).toBe("head");
      if (key.endsWith("-helm")) expect(item.over, key).toBe("beard");
      if (key.endsWith("-coif")) expect(item.over, key).toBe("hair");
    }
    expect((ITEMS["wizard-hat"] as Item).over, "a hat leaves the hair at the sides").toBeUndefined();
  });

  it("gives the cape alone the party colour token", () => {
    const wearing = items.filter(([, item]) => Object.values(item.colours).some((c) => c.includes(CAPE_FILL)));
    expect(wearing.map(([key]) => key).sort()).toEqual(["cape", "trimmed-cape"]);
    for (const [, item] of wearing) expect(item.slot).toBe("cape");
  });

  it("has a full set per metal tier and the three mage orders", () => {
    for (const tier of ["bronze", "iron", "steel", "mithril", "adamant", "rune", "dragon"]) {
      for (const piece of ["helm", "platebody", "platelegs", "kiteshield", "scimitar"]) expect(ITEMS, `${tier}-${piece}`).toHaveProperty(`${tier}-${piece}`);
    }
    for (const order of ["wizard", "mystic", "archmage"]) {
      for (const piece of ["hat", "robe", "skirt", "boots"]) expect(ITEMS, `${order}-${piece}`).toHaveProperty(`${order}-${piece}`);
    }
  });
});
