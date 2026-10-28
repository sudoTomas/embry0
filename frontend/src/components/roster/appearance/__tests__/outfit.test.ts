import { describe, expect, it } from "vitest";
import { GEAR_TIERS, ITEMS, type Item } from "../items";
import { LADDERS, STYLE, dress, outfitKey, type CombatStyle, type Outfit } from "../outfit";
import { PRESETS, PRESET_IDS } from "../presets";

const STYLES: CombatStyle[] = ["melee", "ranged", "magic"];
const bronze: Outfit = { tier: "bronze" };
const CODING = { base: "#4a4a52", trim: "#e07b2a" };
const QUEST = { base: "#2f4a8f", trim: "#ece6d4" };

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

  it("swaps the party cape for the best 99's cloak in its colours, trimmed for two or more", () => {
    expect(dress(PRESETS.smith, "melee", { ...bronze, cloak: CODING }).equipment.cape).toBe("cloak:#4a4a52:#e07b2a");
    expect(dress(PRESETS.smith, "melee", { ...bronze, cloak: CODING, trimmed: true }).equipment.cape).toBe("cloak:#4a4a52:#e07b2a:t");
  });

  it("wears the quest cloak when no skill's cloak outranks it, and a Divine trim on any cape", () => {
    expect(dress(PRESETS.smith, "melee", { ...bronze, questCloak: QUEST }).equipment.cape).toBe("cloak:#2f4a8f:#ece6d4");
    expect(dress(PRESETS.smith, "melee", { ...bronze, questCloak: QUEST, cloak: CODING }).equipment.cape).toBe("cloak:#4a4a52:#e07b2a");
    expect(dress(PRESETS.smith, "melee", { ...bronze, tier: "obsidian", divineTrim: true }).equipment.cape).toBe("cape-divine");
    expect(dress(PRESETS.smith, "melee", { ...bronze, cloak: CODING, trimmed: true, divineTrim: true }).equipment.cape).toBe("cloak:#4a4a52:#e07b2a:t:d");
    expect(dress(PRESETS.smith, "melee", { ...bronze, divineTrim: true }).equipment.cape, "no cape at Bronze, nothing to trim").toBeUndefined();
  });

  it("wears the rare it is given on its head", () => {
    expect(dress(PRESETS.warden, "melee", { ...bronze, head: "paper-crown" }).equipment.head).toBe("paper-crown");
    expect(dress(PRESETS.warden, "melee", { ...bronze, head: "harvest-mask" }).equipment.head).toBe("harvest-mask");
    expect(dress(PRESETS.warden, "melee", bronze).equipment.head).toBe("bronze-open-helm");
  });

  it("keys an outfit by everything that changes how it looks", () => {
    expect(outfitKey({ tier: "obsidian", cloak: CODING, trimmed: true, head: "paper-crown" })).toBe("obsidian|cloak:#4a4a52:#e07b2a:t|paper-crown|");
    expect(outfitKey({ ...bronze, questCloak: QUEST, divineTrim: true })).toBe("bronze|qcloak:#2f4a8f:#ece6d4||d");
    expect(outfitKey(bronze)).not.toBe(outfitKey({ ...bronze, tier: "silver" }));
    expect(outfitKey({ ...bronze, cloak: CODING })).not.toBe(outfitKey({ ...bronze, cloak: { ...CODING, base: "#000000" } }));
  });
});
