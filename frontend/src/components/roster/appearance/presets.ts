/**
 * The named adventurers an agent can wear: a level-3 starter, a warrior
 * per metal tier and a maxed champion, three orders of mage, four archers,
 * four extras, and the twelve original avatars the built-in agents wear,
 * each in its class's Bronze kit (outfit.ts dresses it for its tier). Each
 * is a different person at a glance: skin, hair, beard and the cloth under
 * the gear vary, not only the gear. The ids are AVATARS in
 * lib/agentIdentity.ts (the contract-pinned list); a preset missing or
 * extra there fails to compile here.
 */
import type { AvatarId } from "@/lib/agentIdentity";
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
    equipment: { head: "bronze-helm", body: "bronze-chestplate", legs: "bronze-greaves", shield: "bronze-heater-shield", weapon: "bronze-sabre" },
  },
  "silver-warrior": {
    skin: "light", hair: "spiky", beard: null,
    colours: { hair: "blonde", torso: "red", legs: "charcoal", feet: "black" },
    equipment: { head: "silver-helm", body: "silver-chestplate", legs: "silver-greaves", shield: "silver-heater-shield", weapon: "silver-sabre", feet: "metal-boots" },
  },
  "gold-warrior": {
    skin: "brown", hair: "short", beard: "goatee",
    colours: { hair: "black", torso: "blue", legs: "charcoal", feet: "black" },
    equipment: { head: "gold-helm", body: "gold-chestplate", legs: "gold-greaves", shield: "gold-heater-shield", weapon: "gold-sabre", feet: "metal-boots", cape: "cape" },
  },
  "platinum-warrior": {
    skin: "dark", hair: "mohawk", beard: null,
    colours: { hair: "black", torso: "teal", legs: "charcoal", feet: "black" },
    equipment: { head: "platinum-helm", body: "platinum-chestplate", legs: "platinum-greaves", shield: "platinum-heater-shield", weapon: "platinum-sabre", feet: "metal-boots", cape: "cape", neck: "amulet" },
  },
  "diamond-warrior": {
    skin: "light", hair: "long", beard: "full",
    colours: { hair: "auburn", torso: "green", legs: "charcoal", feet: "black" },
    equipment: { head: "diamond-helm", body: "diamond-chestplate", legs: "diamond-greaves", shield: "diamond-heater-shield", weapon: "diamond-sabre", feet: "metal-boots", cape: "cape", neck: "amulet" },
  },
  "obsidian-warrior": {
    skin: "tan", hair: "short", beard: "moustache",
    colours: { hair: "brown", torso: "blue", legs: "charcoal", feet: "black" },
    equipment: { head: "obsidian-helm", body: "obsidian-chestplate", legs: "obsidian-greaves", shield: "obsidian-heater-shield", weapon: "obsidian-sabre", feet: "black-boots", cape: "cape", neck: "amulet" },
  },
  "crimson-warrior": {
    skin: "brown", hair: "curly", beard: "goatee",
    colours: { hair: "black", torso: "red", legs: "charcoal", feet: "black" },
    equipment: { head: "crimson-helm", body: "crimson-chestplate", legs: "crimson-greaves", shield: "crimson-heater-shield", weapon: "crimson-sabre", feet: "black-boots", cape: "cape", neck: "amulet" },
  },
  champion: {
    skin: "light", hair: "short", beard: "full",
    colours: { hair: "grey", torso: "charcoal", legs: "charcoal", feet: "black" },
    equipment: { head: "crested-helm", body: "trimmed-chestplate", legs: "trimmed-greaves", weapon: "greatsword", hands: "gold-gauntlets", feet: "black-boots", cape: "trimmed-cape", neck: "amulet" },
  },
  // mages
  apprentice: {
    skin: "light", hair: "long", beard: null,
    colours: { hair: "blonde", torso: "blue", legs: "blue", feet: "charcoal" },
    equipment: { head: "novice-hat", body: "novice-robe", legs: "novice-skirt", weapon: "staff", feet: "novice-boots" },
  },
  sorcerer: {
    skin: "tan", hair: "short", beard: "goatee",
    colours: { hair: "black", torso: "purple", legs: "purple", feet: "charcoal" },
    equipment: { head: "violet-hat", body: "violet-robe", legs: "violet-skirt", weapon: "orb-staff", feet: "violet-boots", neck: "amulet" },
  },
  archmage: {
    skin: "dark", hair: "long", beard: "long",
    colours: { hair: "white", torso: "purple", legs: "purple", feet: "charcoal" },
    equipment: { head: "archmage-hat", body: "archmage-robe", legs: "archmage-skirt", weapon: "glass-staff", feet: "archmage-boots", neck: "amulet", cape: "cape" },
  },
  // archers
  scout: {
    skin: "light", hair: "spiky", beard: null,
    colours: { hair: "auburn", torso: "green", legs: "umber", feet: "brown" },
    equipment: { body: "leather-body", legs: "leather-leggings", hands: "leather-bracers", weapon: "shortbow", feet: "leather-boots" },
  },
  ranger: {
    skin: "brown", hair: "short", beard: null,
    colours: { hair: "black", torso: "cream", legs: "green", feet: "brown" },
    equipment: { head: "green-hood", body: "green-body", legs: "green-leggings", hands: "green-bracers", weapon: "longbow", feet: "green-boots" },
  },
  marksman: {
    skin: "tan", hair: "short", beard: "goatee",
    colours: { hair: "black", torso: "charcoal", legs: "charcoal", feet: "black" },
    equipment: { head: "black-hood", body: "black-body", legs: "black-leggings", hands: "black-bracers", weapon: "night-bow", feet: "black-boots", cape: "cape" },
  },
  sharpshooter: {
    skin: "light", hair: "long", beard: null,
    colours: { hair: "black", torso: "red", legs: "red", feet: "black" },
    equipment: { body: "hunter-body", legs: "hunter-leggings", hands: "gold-gauntlets", weapon: "crossbow", feet: "hunter-boots", cape: "trimmed-cape", neck: "amulet" },
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
  artisan: {
    skin: "light", hair: "curly", beard: null,
    colours: { hair: "blonde", torso: "green", legs: "umber", feet: "brown" },
    equipment: { weapon: "axe", feet: "leather-boots" },
  },
  miner: {
    skin: "brown", hair: "short", beard: "moustache",
    colours: { hair: "brown", torso: "cream", legs: "charcoal", feet: "black" },
    equipment: { head: "mining-helmet", weapon: "pickaxe", feet: "metal-boots" },
  },
  // the original twelve: melee in bronze plate with an open helm, magic in wizard blue, ranged in leather
  smith: {
    skin: "brown", hair: "short", beard: "full",
    colours: { hair: "black", torso: "umber", legs: "charcoal", feet: "black" },
    equipment: { head: "bronze-open-helm", body: "bronze-chestplate", legs: "bronze-greaves", shield: "bronze-heater-shield", weapon: "bronze-sabre" },
  },
  warden: {
    skin: "dark", hair: "short", beard: "goatee",
    colours: { hair: "black", torso: "red", legs: "charcoal", feet: "black" },
    equipment: { head: "bronze-open-helm", body: "bronze-chestplate", legs: "bronze-greaves", shield: "bronze-heater-shield", weapon: "bronze-sabre" },
  },
  arbiter: {
    skin: "light", hair: "bald", beard: "long",
    colours: { hair: "grey", torso: "teal", legs: "charcoal", feet: "black" },
    equipment: { head: "bronze-open-helm", body: "bronze-chestplate", legs: "bronze-greaves", shield: "bronze-heater-shield", weapon: "bronze-sabre" },
  },
  golem: {
    skin: "stone", hair: "bald", beard: null,
    colours: { hair: "grey", torso: "charcoal", legs: "charcoal", feet: "charcoal" },
    equipment: { head: "bronze-open-helm", body: "bronze-chestplate", legs: "bronze-greaves", shield: "bronze-heater-shield", weapon: "bronze-sabre" },
  },
  seer: {
    skin: "light", hair: "long", beard: null,
    colours: { hair: "white", torso: "purple", legs: "purple", feet: "charcoal" },
    equipment: { head: "novice-hat", body: "novice-robe", legs: "novice-skirt", feet: "novice-boots", weapon: "staff", neck: "amulet" },
  },
  oracle: {
    skin: "brown", hair: "fringe", beard: null,
    colours: { hair: "black", torso: "purple", legs: "charcoal", feet: "charcoal" },
    equipment: { head: "novice-hat", body: "novice-robe", legs: "novice-skirt", feet: "novice-boots", weapon: "staff" },
  },
  scholar: {
    skin: "dark", hair: "short", beard: "moustache",
    colours: { hair: "grey", torso: "cream", legs: "umber", feet: "brown" },
    equipment: { head: "novice-hat", body: "novice-robe", legs: "novice-skirt", feet: "novice-boots", weapon: "staff", neck: "pendant" },
  },
  alchemist: {
    skin: "light", hair: "curly", beard: null,
    colours: { hair: "auburn", torso: "green", legs: "green", feet: "brown" },
    equipment: { head: "novice-hat", body: "novice-robe", legs: "novice-skirt", feet: "novice-boots", weapon: "staff" },
  },
  herald: {
    skin: "tan", hair: "long", beard: null,
    colours: { hair: "blonde", torso: "blue", legs: "cream", feet: "brown" },
    equipment: { body: "leather-body", legs: "leather-leggings", hands: "leather-bracers", feet: "leather-boots", weapon: "shortbow" },
  },
  engineer: {
    skin: "light", hair: "spiky", beard: "goatee",
    colours: { hair: "brown", torso: "charcoal", legs: "blue", feet: "black" },
    equipment: { body: "leather-body", legs: "leather-leggings", hands: "leather-bracers", feet: "leather-boots", weapon: "shortbow" },
  },
  rogue: {
    skin: "tan", hair: "fringe", beard: null,
    colours: { hair: "auburn", torso: "charcoal", legs: "charcoal", feet: "black" },
    equipment: { body: "leather-body", legs: "leather-leggings", hands: "leather-bracers", feet: "leather-boots", weapon: "shortbow" },
  },
  bard: {
    skin: "brown", hair: "curly", beard: "moustache",
    colours: { hair: "blonde", torso: "red", legs: "cream", feet: "brown" },
    equipment: { body: "leather-body", legs: "leather-leggings", hands: "leather-bracers", feet: "leather-boots", weapon: "shortbow" },
  },
} as const satisfies Record<AvatarId, Appearance>;

export type PresetId = keyof typeof PRESETS;
export const PRESET_IDS = Object.keys(PRESETS) as PresetId[];
