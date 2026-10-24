import { describe, expect, it } from "vitest";
import { GEAR_TIERS, ITEMS, type Item } from "../items";
import { LADDERS, RARES, STYLE, dress, outfitKey, type CombatStyle, type Outfit } from "../outfit";
import { PRESETS, PRESET_IDS } from "../presets";

const STYLES: CombatStyle[] = ["melee", "ranged", "magic"];
const bronze: Outfit = { tier: "bronze", capes: [], rares: [] };

function item(id: string): Item {
  const found = ITEMS[id as keyof typeof ITEMS] as Item | undefined;
  if (!found) throw new Error(`no item ${id}`);
  return found;
}

describe("outfits (progression spec 2026-10-24)", () => {
  it("climbs every ladder through every tier with items that exist, each in its slot", () => {
    for (const style of STYLES) {
      for (const tier of GEAR_TIERS) {
        const kit = LADDERS[style](tier);
        expect(kit.body, `${style} ${tier} body`).toBeDefined();
        expect(kit.weapon, `${style} ${tier} weapon`).toBeDefined();
        for (const [slot, id] of Object.entries(kit)) expect(item(id).slot, `${style} ${tier} ${id}`).toBe(slot);
      }
    }
  });

  it("changes the gear at every step of every ladder", () => {
    for (const style of STYLES) {
      const bodies = GEAR_TIERS.map((t) => LADDERS[style](t).body);
      expect(new Set(bodies).size, style).toBe(GEAR_TIERS.length);
    }
  });

  it("gives every preset a style, and none to the four that keep their own look", () => {
    expect(Object.keys(STYLE).sort()).toEqual([...PRESET_IDS].sort());
    expect(PRESET_IDS.filter((id) => STYLE[id] === null).sort()).toEqual(["miner", "monk", "pirate", "skiller"]);
  });

  it("keeps the person and dresses them for the tier", () => {
    const smith = dress(PRESETS.smith, "melee", { ...bronze, tier: "rune" });
    expect(smith.skin).toBe(PRESETS.smith.skin);
    expect(smith.beard).toBe(PRESETS.smith.beard);
    expect(smith.equipment).toMatchObject({ head: "rune-med-helm", body: "rune-platebody", weapon: "rune-scimitar", cape: "cape" });
    const seer = dress(PRESETS.seer, "magic", { ...bronze, tier: "divine" });
    expect(seer.equipment).toMatchObject({ head: "celestial-hat", body: "celestial-robe", weapon: "divine-staff", neck: "amulet" });
    const herald = dress(PRESETS.herald, "ranged", { ...bronze, tier: "adamant" });
    expect(herald.equipment).toMatchObject({ head: "blue-coif", body: "blue-body", weapon: "yew-longbow" });
  });

  it("keeps a styleless avatar's look and tiers its tool", () => {
    expect(dress(PRESETS.skiller, null, { ...bronze, tier: "rune" }).equipment.weapon).toBe("rune-axe");
    expect(dress(PRESETS.miner, null, { ...bronze, tier: "mithril" }).equipment.weapon).toBe("mithril-pickaxe");
    expect(dress(PRESETS.pirate, null, { ...bronze, tier: "steel" }).equipment).toMatchObject({ weapon: "steel-scimitar", head: "tricorne" });
    expect(dress(PRESETS.monk, null, { ...bronze, tier: "divine" }).equipment).toEqual(PRESETS.monk.equipment);
  });

  it("swaps the party cape for the best 99's skill cape, trimmed for two or more", () => {
    expect(dress(PRESETS.smith, "melee", { ...bronze, capes: ["smithing"] }).equipment.cape).toBe("smithing-cape");
    expect(dress(PRESETS.smith, "melee", { ...bronze, capes: ["smithing", "mining"] }).equipment.cape).toBe("smithing-cape-t");
  });

  it("wears the rarest rare on its head", () => {
    expect(RARES[0]).toBe("white-party-hat");
    expect(dress(PRESETS.warden, "melee", { ...bronze, rares: ["holiday-mask", "party-hat"] }).equipment.head).toBe("party-hat");
    expect(dress(PRESETS.warden, "melee", { ...bronze, rares: ["holiday-mask"] }).equipment.head).toBe("holiday-mask");
  });

  it("keys an outfit by everything that changes how it looks", () => {
    expect(outfitKey({ tier: "rune", capes: ["smithing", "mining"], rares: ["party-hat"] })).toBe("rune|smithing,mining|party-hat");
    expect(outfitKey(bronze)).not.toBe(outfitKey({ ...bronze, tier: "iron" }));
  });
});
