/**
 * The named adventurers an agent can wear: a level-3 starter, a warrior
 * per metal tier and a maxed champion, three orders of mage, four archers,
 * and four extras. Each is a different person at a glance: skin, hair,
 * beard and the cloth under the gear vary, not only the gear. The ids
 * are ADVENTURER_AVATARS in lib/agentIdentity.ts (the contract-pinned
 * list); a preset missing or extra there fails to compile here.
 */
import type { AdventurerId } from "@/lib/agentIdentity";
import type { Appearance } from "./types.ts";

export const PRESETS = {
  starter: {
    skin: "light", hair: "short", beard: null,
    colours: { hair: "brown", torso: "cream", legs: "umber", feet: "brown" },
    equipment: {},
  },
  // warriors
  "bronze-warrior": {
    skin: "tan", hair: "short", beard: null,
    colours: { hair: "black", torso: "cream", legs: "umber", feet: "brown" },
    equipment: { head: "bronze-helm", body: "bronze-platebody", legs: "bronze-platelegs", shield: "bronze-kiteshield", weapon: "bronze-scimitar" },
  },
  "iron-warrior": {
    skin: "light", hair: "spiky", beard: null,
    colours: { hair: "blonde", torso: "red", legs: "charcoal", feet: "black" },
    equipment: { head: "iron-helm", body: "iron-platebody", legs: "iron-platelegs", shield: "iron-kiteshield", weapon: "iron-scimitar", feet: "metal-boots" },
  },
  "steel-warrior": {
    skin: "brown", hair: "short", beard: "goatee",
    colours: { hair: "black", torso: "blue", legs: "charcoal", feet: "black" },
    equipment: { head: "steel-helm", body: "steel-platebody", legs: "steel-platelegs", shield: "steel-kiteshield", weapon: "steel-scimitar", feet: "metal-boots", cape: "cape" },
  },
  "mithril-warrior": {
    skin: "dark", hair: "mohawk", beard: null,
    colours: { hair: "black", torso: "teal", legs: "charcoal", feet: "black" },
    equipment: { head: "mithril-helm", body: "mithril-platebody", legs: "mithril-platelegs", shield: "mithril-kiteshield", weapon: "mithril-scimitar", feet: "metal-boots", cape: "cape", neck: "amulet" },
  },
  "adamant-warrior": {
    skin: "light", hair: "long", beard: "full",
    colours: { hair: "auburn", torso: "green", legs: "charcoal", feet: "black" },
    equipment: { head: "adamant-helm", body: "adamant-platebody", legs: "adamant-platelegs", shield: "adamant-kiteshield", weapon: "adamant-scimitar", feet: "metal-boots", cape: "cape", neck: "amulet" },
  },
  "rune-warrior": {
    skin: "tan", hair: "short", beard: "moustache",
    colours: { hair: "brown", torso: "blue", legs: "charcoal", feet: "black" },
    equipment: { head: "rune-helm", body: "rune-platebody", legs: "rune-platelegs", shield: "rune-kiteshield", weapon: "rune-scimitar", feet: "black-boots", cape: "cape", neck: "amulet" },
  },
  "dragon-warrior": {
    skin: "brown", hair: "curly", beard: "goatee",
    colours: { hair: "black", torso: "red", legs: "charcoal", feet: "black" },
    equipment: { head: "dragon-helm", body: "dragon-platebody", legs: "dragon-platelegs", shield: "dragon-kiteshield", weapon: "dragon-scimitar", feet: "black-boots", cape: "cape", neck: "amulet" },
  },
  champion: {
    skin: "light", hair: "short", beard: "full",
    colours: { hair: "grey", torso: "charcoal", legs: "charcoal", feet: "black" },
    equipment: { head: "crested-helm", body: "trimmed-platebody", legs: "trimmed-platelegs", weapon: "greatsword", hands: "gold-gauntlets", feet: "black-boots", cape: "trimmed-cape", neck: "amulet" },
  },
  // mages
  apprentice: {
    skin: "light", hair: "long", beard: null,
    colours: { hair: "blonde", torso: "blue", legs: "blue", feet: "charcoal" },
    equipment: { head: "wizard-hat", body: "wizard-robe", legs: "wizard-skirt", weapon: "staff", feet: "wizard-boots" },
  },
  mystic: {
    skin: "tan", hair: "short", beard: "goatee",
    colours: { hair: "black", torso: "purple", legs: "purple", feet: "charcoal" },
    equipment: { head: "mystic-hat", body: "mystic-robe", legs: "mystic-skirt", weapon: "orb-staff", feet: "mystic-boots", neck: "amulet" },
  },
  archmage: {
    skin: "dark", hair: "long", beard: "long",
    colours: { hair: "white", torso: "purple", legs: "purple", feet: "charcoal" },
    equipment: { head: "archmage-hat", body: "archmage-robe", legs: "archmage-skirt", weapon: "crystal-staff", feet: "archmage-boots", neck: "amulet", cape: "cape" },
  },
  // archers
  scout: {
    skin: "light", hair: "spiky", beard: null,
    colours: { hair: "auburn", torso: "green", legs: "umber", feet: "brown" },
    equipment: { body: "leather-body", legs: "leather-chaps", hands: "leather-vambraces", weapon: "shortbow", feet: "leather-boots" },
  },
  ranger: {
    skin: "brown", hair: "short", beard: null,
    colours: { hair: "black", torso: "cream", legs: "green", feet: "brown" },
    equipment: { head: "green-coif", body: "green-body", legs: "green-chaps", hands: "green-vambraces", weapon: "longbow", feet: "green-boots" },
  },
  marksman: {
    skin: "tan", hair: "short", beard: "goatee",
    colours: { hair: "black", torso: "charcoal", legs: "charcoal", feet: "black" },
    equipment: { head: "black-coif", body: "black-body", legs: "black-chaps", hands: "black-vambraces", weapon: "dark-bow", feet: "black-boots", cape: "cape" },
  },
  sharpshooter: {
    skin: "light", hair: "long", beard: null,
    colours: { hair: "black", torso: "red", legs: "red", feet: "black" },
    equipment: { body: "hunter-body", legs: "hunter-chaps", hands: "gold-gauntlets", weapon: "crossbow", feet: "hunter-boots", cape: "trimmed-cape", neck: "amulet" },
  },
  // extras
  monk: {
    skin: "brown", hair: "bald", beard: "goatee",
    colours: { hair: "grey", torso: "umber", legs: "umber", feet: "umber" },
    equipment: { body: "monk-robe", legs: "monk-skirt", neck: "pendant" },
  },
  pirate: {
    skin: "tan", hair: "long", beard: "full",
    colours: { hair: "black", torso: "red", legs: "charcoal", feet: "black" },
    equipment: { head: "tricorne", body: "striped-shirt", weapon: "cutlass", feet: "black-boots" },
  },
  skiller: {
    skin: "light", hair: "curly", beard: null,
    colours: { hair: "blonde", torso: "green", legs: "umber", feet: "brown" },
    equipment: { weapon: "axe", feet: "leather-boots" },
  },
  miner: {
    skin: "brown", hair: "short", beard: "moustache",
    colours: { hair: "brown", torso: "cream", legs: "charcoal", feet: "black" },
    equipment: { head: "mining-helmet", weapon: "pickaxe", feet: "metal-boots" },
  },
} as const satisfies Record<AdventurerId, Appearance>;

export type PresetId = keyof typeof PRESETS;
export const PRESET_IDS = Object.keys(PRESETS) as PresetId[];
