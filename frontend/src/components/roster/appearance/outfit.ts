/**
 * What a character wears for what it earned (progression spec 2026-10-24).
 * An avatar picks the person (skin, hair, beard, cloth) and a combat style;
 * progression picks the step of that style's ladder, Bronze to Divine:
 * melee climbs plate, ranged climbs leather to scale to pale hide,
 * magic climbs robes. Which step a tier wears, the colours of each cloak and
 * which rare goes on the head are the content pack's (#176), resolved before
 * an outfit reaches here: this module draws art, it names nothing. A skill at
 * the top level replaces the party cape with that skill's cloak (trimmed for
 * two or more), and a rare replaces the head item. An avatar with no style
 * (the monk, the pirate, the artisans) keeps its look and only its tool takes
 * the step's metal.
 */
import type { PresetId } from "./presets.ts";
import { cloakId, type GearTier, type ItemId } from "./items.ts";
import type { Hex } from "./palette.ts";
import type { Appearance, EquipmentSlot } from "./types.ts";

export type CombatStyle = "melee" | "ranged" | "magic";

/** The head items a rare can wear (docs/contracts/progression.json pins the list). */
export const RARE_ART = ["golden-paper-crown", "paper-crown", "harvest-mask"] as const;
export type RareArt = (typeof RARE_ART)[number];

export interface Cloak {
  base: Hex;
  trim: Hex;
}

export interface Outfit {
  /** The gear art step its tier wears. */
  tier: GearTier;
  /** The cloak of its best skill at the top level, if any. */
  cloak?: Cloak | null;
  /** Two skills or more at the top level: the cloak is trimmed. */
  trimmed?: boolean;
  /** Every quest done and this the highest total level: the quest cloak's colours, unless a skill's cloak outranks it. */
  questCloak?: Cloak | null;
  /** The rare it wears on its head. */
  head?: RareArt | null;
  /** The account's cloak trim: every cape trimmed in the Divine metal. */
  divineTrim?: boolean;
}

type Kit = Partial<Record<EquipmentSlot, ItemId>>;

/** From platinum up a character carries the party cape and an amulet, as the warrior presets do. */
function finery(tier: GearTier): Kit {
  return tier === "bronze" || tier === "silver" || tier === "gold" ? {} : { cape: "cape", neck: "amulet" };
}

const MELEE_FEET: Record<GearTier, ItemId | undefined> = {
  bronze: undefined, silver: "metal-boots", gold: "metal-boots", platinum: "metal-boots", diamond: "metal-boots", obsidian: "black-boots", divine: "divine-boots",
};
const HIDES = { bronze: "leather", silver: "hard", gold: "studded", platinum: "green", diamond: "blue", obsidian: "red", divine: "pale" } as const;
const BOWS: Record<GearTier, ItemId> = {
  bronze: "shortbow", silver: "ash-shortbow", gold: "hunting-bow", platinum: "longbow", diamond: "war-bow", obsidian: "composite-bow", divine: "radiant-bow",
};
const ROBES = { bronze: "novice", silver: "dark", gold: "enchanted", platinum: "violet", diamond: "tidal", obsidian: "archmage", divine: "celestial" } as const;
const STAVES: Record<GearTier, ItemId> = {
  bronze: "staff", silver: "silver-staff", gold: "gold-staff", platinum: "orb-staff", diamond: "diamond-staff", obsidian: "glass-staff", divine: "divine-staff",
};

export const LADDERS: Record<CombatStyle, (tier: GearTier) => Kit> = {
  melee: (t) => ({
    head: `${t}-open-helm`, body: `${t}-chestplate`, legs: `${t}-greaves`, shield: `${t}-heater-shield`, weapon: `${t}-sabre`,
    ...(MELEE_FEET[t] ? { feet: MELEE_FEET[t] } : {}),
    ...finery(t),
  }),
  ranged: (t) => {
    const hide = HIDES[t];
    return {
      // Bronze leaves the head bare, so the starting archer shows its hair.
      ...(t === "bronze" ? {} : { head: `${hide}-hood` as const }),
      body: `${hide}-body`, legs: `${hide}-leggings`, hands: `${hide}-bracers`, feet: `${hide}-boots`, weapon: BOWS[t],
      ...finery(t),
    };
  },
  magic: (t) => {
    const order = ROBES[t];
    return { head: `${order}-hat`, body: `${order}-robe`, legs: `${order}-skirt`, feet: `${order}-boots`, weapon: STAVES[t], ...finery(t) };
  },
};

/** Each avatar's combat style; null keeps its own look. */
export const STYLE: Record<PresetId, CombatStyle | null> = {
  starter: "melee",
  "bronze-warrior": "melee", "silver-warrior": "melee", "gold-warrior": "melee", "platinum-warrior": "melee",
  "diamond-warrior": "melee", "obsidian-warrior": "melee", "crimson-warrior": "melee", champion: "melee",
  apprentice: "magic", sorcerer: "magic", archmage: "magic",
  scout: "ranged", ranger: "ranged", marksman: "ranged", sharpshooter: "ranged",
  monk: null, pirate: null, artisan: null, miner: null,
  smith: "melee", warden: "melee", arbiter: "melee", golem: "melee",
  seer: "magic", oracle: "magic", scholar: "magic", alchemist: "magic",
  herald: "ranged", engineer: "ranged", rogue: "ranged", bard: "ranged",
};

/** A styleless avatar's tool, in the tier's metal. */
function retooled(equipment: Kit, tier: GearTier): Kit {
  const tool: Partial<Record<ItemId, ItemId>> = { axe: `${tier}-axe`, pickaxe: `${tier}-pickaxe`, cutlass: `${tier}-sabre` };
  const weapon = equipment.weapon && tool[equipment.weapon];
  return weapon ? { ...equipment, weapon } : { ...equipment };
}

/** The appearance an avatar wears for an outfit: its person, its ladder's step, its cape and its rare. */
export function dress(base: Appearance, style: CombatStyle | null, outfit: Outfit): Appearance {
  const equipment: Kit = style
    ? { ...(base.equipment.neck ? { neck: base.equipment.neck } : {}), ...LADDERS[style](outfit.tier) }
    : retooled(base.equipment, outfit.tier);
  const divine = Boolean(outfit.divineTrim);
  if (outfit.cloak) equipment.cape = cloakId(outfit.cloak, Boolean(outfit.trimmed), divine);
  else if (outfit.questCloak) equipment.cape = cloakId(outfit.questCloak, false, divine);
  else if (divine && (equipment.cape === "cape" || equipment.cape === "trimmed-cape")) equipment.cape = `${equipment.cape}-divine`;
  if (outfit.head) equipment.head = outfit.head;
  return { ...base, equipment };
}

/** One string per outfit, for caching what it composes to. */
export function outfitKey(o: Outfit): string {
  const cape = o.cloak ? cloakId(o.cloak, Boolean(o.trimmed)) : o.questCloak ? `q${cloakId(o.questCloak)}` : "";
  return `${o.tier}|${cape}|${o.head ?? ""}|${o.divineTrim ? "d" : ""}`;
}
