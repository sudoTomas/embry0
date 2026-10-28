/**
 * What a character wears for what it earned (progression spec 2026-10-24).
 * An avatar picks the person (skin, hair, beard, cloth) and a combat style;
 * progression picks the step of that style's ladder, Bronze to Divine:
 * melee climbs plate, ranged climbs leather to scale to pale hide,
 * magic climbs robes. A skill at 99 replaces the party cape with that
 * skill's cape (trimmed for two or more), and a rare replaces the head
 * item. An avatar with no style (the monk, the pirate, the artisans) keeps
 * its look and only its tool takes the tier's metal.
 */
import type { PresetId } from "./presets.ts";
import type { GearTier, ItemId } from "./items.ts";
import type { SkillId } from "./palette.ts";
import type { Appearance, EquipmentSlot } from "./types.ts";

export type CombatStyle = "melee" | "ranged" | "magic";

/** The rares, rarest first: the first one a character has is the one it wears. */
export const RARES = ["golden-paper-crown", "paper-crown", "harvest-mask"] as const;
export type Rare = (typeof RARES)[number];

export interface Outfit {
  tier: GearTier;
  /** Its skills at 99, best first: the first one's mastery cloak is worn, two or more trim it. */
  capes: SkillId[];
  rares: Rare[];
  /** Every quest done and this the highest total level: the quest cloak, unless a mastery cloak outranks it. */
  questCloak?: boolean;
  /** Divine Warden done: every cape trimmed in the Divine metal. */
  divineTrim?: boolean;
}

type Kit = Partial<Record<EquipmentSlot, ItemId>>;
/** A cape a Divine trim can go on: every cape but one already trimmed in it. */
type CapeItem = Exclude<Extract<ItemId, "cape" | "trimmed-cape" | "quest-cloak" | `${SkillId}-cloak` | `${SkillId}-cloak-t`>, `${string}-divine`>;

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
  const [cape] = outfit.capes;
  if (cape) equipment.cape = outfit.capes.length > 1 ? `${cape}-cloak-t` : `${cape}-cloak`;
  else if (outfit.questCloak) equipment.cape = "quest-cloak";
  if (outfit.divineTrim && equipment.cape) equipment.cape = `${equipment.cape as CapeItem}-divine`;
  const rare = RARES.find((r) => outfit.rares.includes(r));
  if (rare) equipment.head = rare;
  return { ...base, equipment };
}

/** One string per outfit, for caching what it composes to. */
export function outfitKey(o: Outfit): string {
  return `${o.tier}|${o.capes.join(",")}|${o.rares.join(",")}|${o.questCloak ? "q" : ""}${o.divineTrim ? "d" : ""}`;
}
