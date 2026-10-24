/**
 * What a character wears for what it earned (progression spec 2026-10-24).
 * An avatar picks the person (skin, hair, beard, cloth) and a combat style;
 * progression picks the step of that style's ladder, Bronze to Divine:
 * melee climbs plate, ranged climbs leather to dragonhide to blessed hide,
 * magic climbs robes. A skill at 99 replaces the party cape with that
 * skill's cape (trimmed for two or more), and a rare replaces the head
 * item. An avatar with no style (the monk, the pirate, the skillers) keeps
 * its look and only its tool takes the tier's metal.
 */
import type { PresetId } from "./presets.ts";
import type { GearTier, ItemId } from "./items.ts";
import type { Echo } from "./palette.ts";
import type { Appearance, EquipmentSlot } from "./types.ts";

export type CombatStyle = "melee" | "ranged" | "magic";

/** The rares, rarest first: the first one a character has is the one it wears. */
export const RARES = ["white-party-hat", "party-hat", "holiday-mask"] as const;
export type Rare = (typeof RARES)[number];

export interface Outfit {
  tier: GearTier;
  /** The echoes of its skills at 99, best first: the first is worn, two or more trim it. */
  capes: Echo[];
  rares: Rare[];
  /** Every quest done and this the highest total level: the quest cape, unless a skill cape outranks it. */
  questCape?: boolean;
  /** Divine Slayer done: every cape trimmed in the Divine metal. */
  divineTrim?: boolean;
}

type Kit = Partial<Record<EquipmentSlot, ItemId>>;
/** A cape a Divine trim can go on: every cape but one already trimmed in it. */
type CapeItem = Exclude<Extract<ItemId, "cape" | "trimmed-cape" | "quest-cape" | `${Echo}-cape` | `${Echo}-cape-t`>, `${string}-divine`>;

/** From mithril up a character carries the party cape and an amulet, as the warrior presets do. */
function finery(tier: GearTier): Kit {
  return tier === "bronze" || tier === "iron" || tier === "steel" ? {} : { cape: "cape", neck: "amulet" };
}

const MELEE_FEET: Record<GearTier, ItemId | undefined> = {
  bronze: undefined, iron: "metal-boots", steel: "metal-boots", mithril: "metal-boots", adamant: "metal-boots", rune: "black-boots", divine: "divine-boots",
};
const HIDES = { bronze: "leather", iron: "hard", steel: "studded", mithril: "green", adamant: "blue", rune: "red", divine: "blessed" } as const;
const BOWS: Record<GearTier, ItemId> = {
  bronze: "shortbow", iron: "oak-shortbow", steel: "willow-longbow", mithril: "longbow", adamant: "yew-longbow", rune: "magic-longbow", divine: "crystal-bow",
};
const ROBES = { bronze: "wizard", iron: "dark", steel: "enchanted", mithril: "mystic", adamant: "astral", rune: "archmage", divine: "celestial" } as const;
const STAVES: Record<GearTier, ItemId> = {
  bronze: "staff", iron: "iron-staff", steel: "steel-staff", mithril: "orb-staff", adamant: "adamant-staff", rune: "crystal-staff", divine: "divine-staff",
};

export const LADDERS: Record<CombatStyle, (tier: GearTier) => Kit> = {
  melee: (t) => ({
    head: `${t}-med-helm`, body: `${t}-platebody`, legs: `${t}-platelegs`, shield: `${t}-kiteshield`, weapon: `${t}-scimitar`,
    ...(MELEE_FEET[t] ? { feet: MELEE_FEET[t] } : {}),
    ...finery(t),
  }),
  ranged: (t) => {
    const hide = HIDES[t];
    return {
      // Bronze leaves the head bare, so the starting archer shows its hair.
      ...(t === "bronze" ? {} : { head: `${hide}-coif` as const }),
      body: `${hide}-body`, legs: `${hide}-chaps`, hands: `${hide}-vambraces`, feet: `${hide}-boots`, weapon: BOWS[t],
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
  "bronze-warrior": "melee", "iron-warrior": "melee", "steel-warrior": "melee", "mithril-warrior": "melee",
  "adamant-warrior": "melee", "rune-warrior": "melee", "dragon-warrior": "melee", champion: "melee",
  apprentice: "magic", mystic: "magic", archmage: "magic",
  scout: "ranged", ranger: "ranged", marksman: "ranged", sharpshooter: "ranged",
  monk: null, pirate: null, skiller: null, miner: null,
  smith: "melee", warden: "melee", arbiter: "melee", golem: "melee",
  seer: "magic", oracle: "magic", scholar: "magic", alchemist: "magic",
  herald: "ranged", engineer: "ranged", rogue: "ranged", bard: "ranged",
};

/** A styleless avatar's tool, in the tier's metal. */
function retooled(equipment: Kit, tier: GearTier): Kit {
  const tool: Partial<Record<ItemId, ItemId>> = { axe: `${tier}-axe`, pickaxe: `${tier}-pickaxe`, cutlass: `${tier}-scimitar` };
  const weapon = equipment.weapon && tool[equipment.weapon];
  return weapon ? { ...equipment, weapon } : { ...equipment };
}

/** The appearance an avatar wears for an outfit: its person, its ladder's step, its cape and its rare. */
export function dress(base: Appearance, style: CombatStyle | null, outfit: Outfit): Appearance {
  const equipment: Kit = style
    ? { ...(base.equipment.neck ? { neck: base.equipment.neck } : {}), ...LADDERS[style](outfit.tier) }
    : retooled(base.equipment, outfit.tier);
  const [cape] = outfit.capes;
  if (cape) equipment.cape = outfit.capes.length > 1 ? `${cape}-cape-t` : `${cape}-cape`;
  else if (outfit.questCape) equipment.cape = "quest-cape";
  if (outfit.divineTrim && equipment.cape) equipment.cape = `${equipment.cape as CapeItem}-divine`;
  const rare = RARES.find((r) => outfit.rares.includes(r));
  if (rare) equipment.head = rare;
  return { ...base, equipment };
}

/** One string per outfit, for caching what it composes to. */
export function outfitKey(o: Outfit): string {
  return `${o.tier}|${o.capes.join(",")}|${o.rares.join(",")}|${o.questCape ? "q" : ""}${o.divineTrim ? "d" : ""}`;
}
