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
    expect(PRESET_IDS.filter((id) => STYLE[id] === null).sort()).toEqual(["artisan", "miner", "monk", "pirate"]);
  });

  it("keeps the person and dresses them for the tier", () => {
    const smith = dress(PRESETS.smith, "melee", { ...bronze, tier: "obsidian" });
    expect(smith.skin).toBe(PRESETS.smith.skin);
    expect(smith.beard).toBe(PRESETS.smith.beard);
    expect(smith.equipment).toMatchObject({ head: "obsidian-open-helm", body: "obsidian-chestplate", weapon: "obsidian-sabre", cape: "cape" });
    const seer = dress(PRESETS.seer, "magic", { ...bronze, tier: "divine" });
    expect(seer.equipment).toMatchObject({ head: "celestial-hat", body: "celestial-robe", weapon: "divine-staff", neck: "amulet" });
    const herald = dress(PRESETS.herald, "ranged", { ...bronze, tier: "diamond" });
    expect(herald.equipment).toMatchObject({ head: "blue-hood", body: "blue-body", weapon: "war-bow" });
  });

  it("keeps a styleless avatar's look and tiers its tool", () => {
    expect(dress(PRESETS.artisan, null, { ...bronze, tier: "obsidian" }).equipment.weapon).toBe("obsidian-axe");
    expect(dress(PRESETS.miner, null, { ...bronze, tier: "platinum" }).equipment.weapon).toBe("platinum-pickaxe");
    expect(dress(PRESETS.pirate, null, { ...bronze, tier: "gold" }).equipment).toMatchObject({ weapon: "gold-sabre", head: "tricorne" });
    expect(dress(PRESETS.monk, null, { ...bronze, tier: "divine" }).equipment).toEqual(PRESETS.monk.equipment);
  });

  it("swaps the party cape for the best 99's mastery cloak, trimmed for two or more", () => {
    expect(dress(PRESETS.smith, "melee", { ...bronze, capes: ["coding"] }).equipment.cape).toBe("coding-cloak");
    expect(dress(PRESETS.smith, "melee", { ...bronze, capes: ["coding", "databases"] }).equipment.cape).toBe("coding-cloak-t");
  });

  it("wears the quest cloak when no mastery cloak outranks it, and a Divine trim on any cape", () => {
    expect(dress(PRESETS.smith, "melee", { ...bronze, questCloak: true }).equipment.cape).toBe("quest-cloak");
    expect(dress(PRESETS.smith, "melee", { ...bronze, questCloak: true, capes: ["coding"] }).equipment.cape).toBe("coding-cloak");
    expect(dress(PRESETS.smith, "melee", { ...bronze, tier: "obsidian", divineTrim: true }).equipment.cape).toBe("cape-divine");
    expect(dress(PRESETS.smith, "melee", { ...bronze, capes: ["coding", "databases"], divineTrim: true }).equipment.cape).toBe("coding-cloak-t-divine");
    expect(dress(PRESETS.smith, "melee", { ...bronze, divineTrim: true }).equipment.cape, "no cape at Bronze, nothing to trim").toBeUndefined();
  });

  it("wears the rarest rare on its head", () => {
    expect(RARES[0]).toBe("golden-paper-crown");
    expect(dress(PRESETS.warden, "melee", { ...bronze, rares: ["harvest-mask", "paper-crown"] }).equipment.head).toBe("paper-crown");
    expect(dress(PRESETS.warden, "melee", { ...bronze, rares: ["harvest-mask"] }).equipment.head).toBe("harvest-mask");
  });

  it("keys an outfit by everything that changes how it looks", () => {
    expect(outfitKey({ tier: "obsidian", capes: ["coding", "databases"], rares: ["paper-crown"] })).toBe("obsidian|coding,databases|paper-crown|");
    expect(outfitKey({ ...bronze, questCloak: true, divineTrim: true })).toBe("bronze|||qd");
    expect(outfitKey(bronze)).not.toBe(outfitKey({ ...bronze, tier: "silver" }));
  });
});
