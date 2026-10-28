import { describe, expect, it } from "vitest";
import { ITEMS, cloakId, itemOf, type Item } from "../items";
import { CAPE_FILL, METAL, dark } from "../palette";
import { GRID, type EquipmentSlot, type Frame } from "../types";

const SLOTS: EquipmentSlot[] = ["head", "cape", "neck", "weapon", "body", "shield", "legs", "hands", "feet"];
const items = Object.entries(ITEMS) as [string, Item][];

function framesOf(item: Item): [string, Frame][] {
  const out: [string, Frame][] = [];
  if (item.layer) out.push(["layer", item.layer]);
  if (item.shoulders) out.push(["shoulders", item.shoulders]);
  if (item.drape) out.push(["drape", item.drape]);
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
      if (key.endsWith("-open-helm")) expect(item.over, `${key} shows the face and beard`).toBe("hair");
      else if (key.endsWith("-helm")) expect(item.over, key).toBe("beard");
      if (key.endsWith("-hood")) expect(item.over, key).toBe("hair");
    }
    expect((ITEMS["novice-hat"] as Item).over, "a hat leaves the hair at the sides").toBeUndefined();
  });

  it("gives the party colour token to the cape and the paper crown alone", () => {
    const wearing = items.filter(([, item]) => Object.values(item.colours).some((c) => c.includes(CAPE_FILL)));
    expect(wearing.map(([key]) => key).sort()).toEqual(["cape", "cape-divine", "paper-crown", "trimmed-cape", "trimmed-cape-divine"]);
  });

  it("draws a cloak in any colours the content pack gives it, plain or trimmed (#176)", () => {
    const colours = { base: "#4a4a52", trim: "#e07b2a" };
    const plain = itemOf(cloakId(colours))!;
    const trimmed = itemOf(cloakId(colours, true))!;
    expect(plain.slot).toBe("cape");
    expect(plain.colours).toEqual({ o: "#15121c", c: "#4a4a52", C: dark("#4a4a52"), "5": "#e07b2a" });
    expect(plain.drape, "its drape shows from the front").toBeDefined();
    expect(trimmed.layer).not.toEqual(plain.layer);
    expect(itemOf(cloakId(colours)), "composed once").toBe(plain);
    expect(itemOf("cloak:red:blue"), "colours that are not #rrggbb draw nothing").toBeUndefined();
  });

  it("has a full set per metal tier and the three mage orders", () => {
    for (const tier of ["bronze", "silver", "gold", "platinum", "diamond", "obsidian", "crimson", "divine"]) {
      for (const piece of ["helm", "chestplate", "greaves", "heater-shield", "sabre"]) expect(ITEMS, `${tier}-${piece}`).toHaveProperty(`${tier}-${piece}`);
    }
    for (const order of ["novice", "violet", "archmage"]) {
      for (const piece of ["hat", "robe", "skirt", "boots"]) expect(ITEMS, `${order}-${piece}`).toHaveProperty(`${order}-${piece}`);
    }
  });
});

describe("quest cloak and Divine trim (progression spec 2026-10-24)", () => {
  it("gives every cape a Divine-trimmed form on the trimmed cape, in the Divine metal", () => {
    const capes = items.filter(([, item]) => item.slot === "cape");
    for (const [key, item] of capes.filter(([key]) => !key.endsWith("-divine"))) {
      const divine = ITEMS[`${key}-divine` as keyof typeof ITEMS] as Item;
      expect(divine, key).toBeDefined();
      expect(divine.colours["5"], key).toBe(METAL.divine);
      expect(divine.colours.c, key).toBe(item.colours.c);
    }
    const cloak = itemOf(cloakId({ base: "#2f4a8f", trim: "#ece6d4" }, false, true))!;
    expect(cloak.colours["5"], "a cloak's trim too").toBe(METAL.divine);
    expect(cloak.layer).toEqual(itemOf(cloakId({ base: "#2f4a8f", trim: "#ece6d4" }, true))!.layer);
  });
});
