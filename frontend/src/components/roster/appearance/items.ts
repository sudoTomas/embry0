/**
 * The equipment catalogue: everything an adventurer can wear, one shape per
 * kind of thing drawn once over the three-tone key of palette.ts, so a
 * helm is every metal tier of itself and a robe every order of mage. Every
 * shape is original pixel art. Positions follow the body kit of parts.ts:
 * the weapon hand is the left (viewer's left) one, down at x 8..10 rows
 * 19..21 and raised at x 7..9 rows 7..8; the shield arm is the right one.
 */
import { CAPE_FILL, CAPE_SHADE, GOLD, HIDE, LEATHER, MAGE, METAL, OUTLINE, QUEST_CLOAK, ROBE, MASTERY_CLOAK, STRING, WOOD, dark, light, tones, type SkillId, type Hex, type MetalTier } from "./palette.ts";
import { FEET, LEGS } from "./parts.ts";
import type { ColourKey, EquipmentSlot, Frame } from "./types.ts";

export interface Item {
  id: string;
  slot: EquipmentSlot;
  /** The worn layer, for every slot but the weapon. */
  layer?: Frame;
  /** A weapon's two layers: in the hand at rest, and raised (the tool and done poses). */
  weapon?: { rest: Frame; raised: Frame };
  /** A body item's pauldrons, drawn over the arms. */
  shoulders?: Frame;
  /** A mastery cloak's drape over the shoulders, drawn over the arms, so its colours show from the front. */
  drape?: Frame;
  colours: ColourKey;
  /** `hair`: the item covers the hair. `beard`: a full helm, covering hair and beard both. */
  over?: "hair" | "beard";
  /** A body item's sleeve colour, replacing the torso colour slot on the arms. */
  sleeves?: Hex;
  /** A hands item's colour, replacing the skin on the hands. */
  hands?: Hex;
}

// --- head slot ---------------------------------------------------------------

/** A full helm: metal all round, a T-shaped slit, covering hair and beard. */
const FULL_HELM: Frame = { x: 10, y: 1, rows: [
  "..oooooooo..",
  ".o32222221o.",
  "o3322222211o",
  "o3322222211o",
  "o32oooooo11o",
  "o3222oo2211o",
  "o3222oo2211o",
  "o3222oo2211o",
  ".o32222221o.",
  "..oooooooo..",
] };

/** The champion's helm: a gold band, gold cheek guards and a crest. */
const CRESTED_HELM: Frame = { x: 10, y: 1, rows: [
  "....o5555o..",
  ".o35555551o.",
  "o3322222211o",
  "o3522222251o",
  "o35oooooo51o",
  "o3522oo2251o",
  "o3522oo2251o",
  "o3222oo2211o",
  ".o35555551o.",
  "..oooooooo..",
] };

/** A pointed hat with a bent tip and a wide brim; the crown clears the hair under it. */
const WIZARD_HAT: Frame = { x: 9, y: 1, rows: [
  "..xxxxxo22oxx.",
  "..xxxo2222oxx.",
  "..xxo222221ox.",
  "o222222222221o",
  "oo..........oo",
] };
/** The mystic's hat: a trim band above the brim. */
const BANDED_HAT: Frame = { x: 9, y: 1, rows: [
  "..xxxxxo22oxx.",
  "..xxxo2222oxx.",
  "..xxo555551ox.",
  "o222222222221o",
  "oo..........oo",
] };
/** The archmage's hat: the band carries a gem and the brim a trim edge. */
const GEMMED_HAT: Frame = { x: 9, y: 1, rows: [
  "..xxxxxo22oxx.",
  "..xxxo2222oxx.",
  "..xxo556651ox.",
  "o555555555554o",
  "oo..........oo",
] };

/** A hood framing the face, covering the hair. */
const HOOD: Frame = { x: 10, y: 1, rows: [
  "..oooooooo..",
  ".o32222221o.",
  "o3222222221o",
  "o3........1o",
  "o3........1o",
  "o3........1o",
  "o3........1o",
  "o3........1o",
  ".o........o.",
] };

/** A tricorne with a trim band. */
const TRICORNE: Frame = { x: 8, y: 1, rows: [
  ".....oooooo.....",
  "..oooxx2222xooo.",
  ".o222222222222o.",
  "o55555555555555o",
  "oo............oo",
] };

/** A hard hat with a lamp on the brow. */
const MINING_HELMET: Frame = { x: 11, y: 1, rows: [
  "..oooooo..",
  ".o322221o.",
  "o32266221o",
  "oooooooooo",
] };

/** An open-faced helm: the crown and cheek guards in metal, the face and beard showing. The ladder's helm (progression spec 2026-10-24), so a character stays recognisable in its gear. */
const OPEN_HELM: Frame = { x: 10, y: 1, rows: [
  "..oooooooo..",
  ".o32222221o.",
  "o3322222211o",
  "o32oooooo11o",
  "o3o......o1o",
  "o3o......o1o",
  ".o........o.",
] };

/** A paper crown with three points: the rare a merge on 25 December earns. */
const PAPER_CROWN: Frame = { x: 11, y: 0, rows: [
  ".o..oo..o.",
  "o2oo22oo2o",
  "o32222221o",
  "o11111111o",
  "oooooooooo",
] };

/** A grinning mask over the whole head: the rare a merge on 31 October earns. */
const HARVEST_MASK: Frame = { x: 11, y: 2, rows: [
  ".oooooooo.",
  "o32222221o",
  "o32222221o",
  "o2oo22oo1o",
  "o22222221o",
  "o2o2o2o21o",
  ".o222221o.",
  "..oooooo..",
] };

// --- body slot ---------------------------------------------------------------

/** A chest plate with a centre seam, a collar and a belt line. Its pauldrons go over the arms. */
const CHESTPLATE: Frame = { x: 11, y: 11, rows: [
  "oooooooooo",
  "o33222211o",
  "o32222211o",
  "o32222211o",
  "o32212211o",
  "o32212211o",
  "o32212211o",
  "o32222211o",
  "o11111111o",
] };
const PAULDRONS: Frame = { x: 7, y: 11, rows: [
  "oooo..........oooo",
  "o322..........221o",
  "o322..........221o",
  ".ooo..........ooo.",
] };

/** The champion's plate: gold collar, seam and belt. */
const TRIMMED_CHESTPLATE: Frame = { x: 11, y: 11, rows: [
  "oooooooooo",
  "o35555551o",
  "o32222211o",
  "o32252211o",
  "o32252211o",
  "o32252211o",
  "o32252211o",
  "o32222211o",
  "o55555555o",
] };
const TRIMMED_PAULDRONS: Frame = { x: 7, y: 11, rows: [
  "oooo..........oooo",
  "o355..........551o",
  "o322..........221o",
  ".ooo..........ooo.",
] };

/** A robe top: a V collar in the trim and a sash. */
const ROBE_TOP: Frame = { x: 11, y: 11, rows: [
  "oooooooooo",
  "o32522521o",
  "o32255221o",
  "o32222221o",
  "o32222221o",
  "o32222221o",
  "o32222221o",
  "o32222221o",
  "o55555555o",
] };

/** A laced jerkin. The shirt's sleeves show on the arms. */
const JERKIN: Frame = { x: 11, y: 11, rows: [
  "oooooooooo",
  "o32222221o",
  "o32225221o",
  "o32252221o",
  "o32225221o",
  "o32252221o",
  "o32222221o",
  "o32222221o",
  "o11111111o",
] };

/** A hide body with a quiver strap across it. */
const STRAPPED_BODY: Frame = { x: 11, y: 11, rows: [
  "oooooooooo",
  "o35222221o",
  "o32522221o",
  "o32252221o",
  "o32225221o",
  "o32222521o",
  "o32222251o",
  "o32222221o",
  "o55555555o",
] };

/** A striped shirt. */
const STRIPED_SHIRT: Frame = { x: 11, y: 11, rows: [
  "oooooooooo",
  "o32222221o",
  "o65555554o",
  "o32222221o",
  "o65555554o",
  "o32222221o",
  "o65555554o",
  "o32222221o",
  "o11111111o",
] };

// --- legs slot ---------------------------------------------------------------

/** Greaves with a trim belt and knee plates. */
const TRIMMED_LEGS: Frame = { x: 11, y: 20, rows: [
  "o55555555o",
  "o322oo221o",
  "o322oo221o",
  "o322oo221o",
  "o355oo551o",
  "o322oo221o",
  "o322oo221o",
  "o322oo221o",
  "o322oo221o",
] };

/** A robe's skirt: one piece over both legs, flaring to the hem; the boots show under it. */
const SKIRT: Frame = { x: 10, y: 20, rows: [
  ".o11111111o.",
  ".o32222221o.",
  ".o32222221o.",
  ".o32222221o.",
  ".o32222221o.",
  "o3322222211o",
  "o3322222211o",
  "o3322222211o",
  "o3322222211o",
  "o3322222211o",
  ".oooooooooo.",
] };
/** The mystic's skirt: a trim band above the hem. */
const BANDED_SKIRT: Frame = { x: 10, y: 20, rows: [
  ".o11111111o.",
  ".o32222221o.",
  ".o32222221o.",
  ".o32222221o.",
  ".o32222221o.",
  "o3322222211o",
  "o3322222211o",
  "o3322222211o",
  "o5555555555o",
  "o3322222211o",
  ".oooooooooo.",
] };
/** The archmage's skirt: trim hem and a glyph row. */
const RUNED_SKIRT: Frame = { x: 10, y: 20, rows: [
  ".o55555555o.",
  ".o32222221o.",
  ".o32522521o.",
  ".o32222221o.",
  ".o32222221o.",
  "o3322522211o",
  "o3322222211o",
  "o3325222521o",
  "o3322222211o",
  "o5555555555o",
  ".oooooooooo.",
] };

// --- shield slot -------------------------------------------------------------

/** A heater shield on the right arm: square shoulders tapering to a point, an emblem in the dark tone. */
const HEATER_SHIELD: Frame = { x: 20, y: 12, rows: [
  "oooooooo",
  "o322221o",
  "o321121o",
  "o321121o",
  "o322221o",
  "o322221o",
  ".o3221o.",
  ".o3221o.",
  "..o221o.",
  "..o21o..",
  "...o1o..",
  "...oo...",
] };

// --- neck and cape slots -----------------------------------------------------

const AMULET: Frame = { x: 14, y: 10, rows: [
  "o..o",
  ".55.",
  ".rr.",
] };
const PENDANT: Frame = { x: 14, y: 10, rows: [
  "o..o",
  ".55.",
  ".44.",
] };

/** A cloak: hidden behind the body at the shoulders, flaring past the arms to the hem. */
const CAPE: Frame = { x: 5, y: 11, rows: [
  "...oooooooooooooooo...",
  "...occccccccccccCCo...",
  "...occccccccccccCCo...",
  "...occccccccccccCCo...",
  "...occccccccccccCCo...",
  "...occccccccccccCCo...",
  "..occccccccccccccCCo..",
  "..occccccccccccccCCo..",
  "..occccccccccccccCCo..",
  "..occccccccccccccCCo..",
  ".occccccccccccccccCCo.",
  ".occccccccccccccccCCo.",
  ".occccccccccccccccCCo.",
  ".occccccccccccccccCCo.",
  "occccccccccccccccccCCo",
  "occccccccccccccccccCCo",
  "oooooooooooooooooooooo",
] };
/** The same cloak with a trim edge and hem. */
const TRIMMED_CAPE: Frame = { x: 5, y: 11, rows: [
  "...oooooooooooooooo...",
  "...o5cccccccccccC5o...",
  "...o5cccccccccccC5o...",
  "...o5cccccccccccC5o...",
  "...o5cccccccccccC5o...",
  "...o5cccccccccccC5o...",
  "..o5cccccccccccccC5o..",
  "..o5cccccccccccccC5o..",
  "..o5cccccccccccccC5o..",
  "..o5cccccccccccccC5o..",
  ".o5cccccccccccccccC5o.",
  ".o5cccccccccccccccC5o.",
  ".o5cccccccccccccccC5o.",
  ".o5cccccccccccccccC5o.",
  "o5cccccccccccccccccC5o",
  "o55555555555555555555o",
  "oooooooooooooooooooooo",
] };

/** A mastery cloak over the shoulders, where the pauldrons sit: the colours that say which 99 it is. */
const CAPE_DRAPE: Frame = { x: 7, y: 11, rows: [
  "oooo..........oooo",
  "occ5..........5cco",
  "oCC5..........5CCo",
  ".ooo..........ooo.",
] };

// --- weapons: rest in the down hand, raised above the up hand ----------------

/** A curved single-edged blade, widening toward the tip. */
const SABRE = {
  rest: { x: 5, y: 18, rows: [
    "...o5o.",
    ".......",
    ".......",
    ".......",
    ".o5555o",
    "...o2o.",
    "...o2o.",
    "..o23o.",
    "..o23o.",
    ".o223o.",
    ".o23o..",
    "o23o...",
    "o2o....",
    "oo.....",
  ] },
  raised: { x: 3, y: 0, rows: [
    "o2o.....",
    ".o23o...",
    "..o23o..",
    "...o23o.",
    "....o23o",
    "....o23o",
    "..o5555o",
    "........",
    "........",
    "....o5o.",
  ] },
};

/** A two-handed sword: long grip, wide guard, a blade three cells across. */
const GREATSWORD = {
  rest: { x: 4, y: 17, rows: [
    "....o5o.",
    "....o4o.",
    "........",
    "........",
    "........",
    ".o55555o",
    "...o321o",
    "...o321o",
    "...o321o",
    "...o321o",
    "...o321o",
    "...o321o",
    "...o321o",
    "....o2o.",
    ".....o..",
  ] },
  raised: { x: 4, y: 0, rows: [
    "...o2o.",
    "..o321o",
    "..o321o",
    "..o321o",
    "..o321o",
    "..o321o",
    "o55555o",
    ".......",
    ".......",
    "...o5o.",
    "...o4o.",
  ] },
};

/** A plain staff with a knob. */
const STAFF = {
  rest: { x: 7, y: 2, rows: [
    "ooo",
    "o5o",
    "o2o", "o2o", "o2o", "o2o", "o2o", "o2o", "o2o", "o2o", "o2o", "o2o", "o2o", "o2o", "o2o",
    "...", "...", "...",
    "o2o", "o2o", "o2o", "o2o", "o2o", "o2o", "o2o", "o2o", "o2o",
  ] },
  raised: { x: 7, y: 0, rows: [
    "ooo",
    "o5o",
    "o2o", "o2o", "o2o", "o2o", "o2o",
    "...", "...",
    "o2o", "o2o", "o2o", "o2o", "o2o", "o2o", "o2o", "o2o", "o2o", "o2o",
  ] },
};

/** A staff topped with an orb. */
const ORB_STAFF = {
  rest: { x: 6, y: 1, rows: [
    ".ooo.",
    "o565o",
    "o555o",
    ".o2o.", ".o2o.", ".o2o.", ".o2o.", ".o2o.", ".o2o.", ".o2o.", ".o2o.", ".o2o.", ".o2o.", ".o2o.", ".o2o.", ".o2o.",
    ".....", ".....", ".....",
    ".o2o.", ".o2o.", ".o2o.", ".o2o.", ".o2o.", ".o2o.", ".o2o.", ".o2o.", ".o2o.",
  ] },
  raised: { x: 6, y: 0, rows: [
    ".ooo.",
    "o565o",
    "o555o",
    ".o2o.", ".o2o.", ".o2o.", ".o2o.",
    ".....", ".....",
    ".o2o.", ".o2o.", ".o2o.", ".o2o.", ".o2o.", ".o2o.", ".o2o.", ".o2o.", ".o2o.", ".o2o.",
  ] },
};

/** A staff with a crystal that glows: light cells float beside it. */
const GLASS_STAFF = {
  rest: { x: 5, y: 1, rows: [
    "6.ooo.6",
    ".o666o.",
    "o56665o",
    ".o565o.",
    "6.o5o.6",
    "..o2o..", "..o2o..", "..o2o..", "..o2o..", "..o2o..", "..o2o..", "..o2o..", "..o2o..", "..o2o..", "..o2o..", "..o2o..", "..o2o..", "..o2o..",
    ".......", ".......", ".......",
    "..o2o..", "..o2o..", "..o2o..", "..o2o..", "..o2o..", "..o2o..", "..o2o..", "..o2o..", "..o2o..",
  ] },
  raised: { x: 5, y: 0, rows: [
    ".o666o.",
    "o56665o",
    ".o565o.",
    "6.o5o.6",
    "..o2o..", "..o2o..", "..o2o..",
    ".......", ".......",
    "..o2o..", "..o2o..", "..o2o..", "..o2o..", "..o2o..", "..o2o..", "..o2o..", "..o2o..", "..o2o..", "..o2o..",
  ] },
};

/** A short recurve bow with its string, held at the grip. */
const SHORTBOW = {
  rest: { x: 5, y: 12, rows: [
    "..oo.",
    ".o2o5",
    "o2o.5",
    "o2o.5",
    "o2o.5",
    "o2o.5",
    ".o2o5",
    ".....",
    ".....",
    ".....",
    ".o2o5",
    "o2o.5",
    "o2o.5",
    "o2o.5",
    "o2o.5",
    ".o2o5",
    "..oo.",
  ] },
  raised: { x: 4, y: 0, rows: [
    "..oo.",
    ".o2o5",
    "o2o.5",
    "o2o.5",
    "o2o.5",
    ".o2o5",
    "..o25",
    ".....",
    ".....",
    "..o25",
    ".o2o5",
    "o2o.5",
    "o2o.5",
    "o2o.5",
    ".o2o5",
    "..oo.",
  ] },
};

/** A longbow: taller limbs, the same grip. */
const LONGBOW = {
  rest: { x: 5, y: 8, rows: [
    "..oo.",
    ".o2o5",
    "o2o.5",
    "o2o.5",
    "o2o.5",
    "o2o.5",
    "o2o.5",
    "o2o.5",
    "o2o.5",
    "o2o.5",
    ".o2o5",
    ".....",
    ".....",
    ".....",
    ".o2o5",
    "o2o.5",
    "o2o.5",
    "o2o.5",
    "o2o.5",
    "o2o.5",
    "o2o.5",
    "o2o.5",
    ".o2o5",
    "..oo.",
  ] },
  raised: { x: 4, y: 0, rows: [
    "..oo.",
    ".o2o5",
    "o2o.5",
    "o2o.5",
    "o2o.5",
    ".o2o5",
    "..o25",
    ".....",
    ".....",
    "..o25",
    ".o2o5",
    "o2o.5",
    "o2o.5",
    "o2o.5",
    "o2o.5",
    "o2o.5",
    "o2o.5",
    "o2o.5",
    ".o2o5",
    "..oo.",
  ] },
};

/** A crossbow: a stock in the hand and a prod across it. */
const CROSSBOW = {
  rest: { x: 4, y: 17, rows: [
    "....o4o....",
    "....o4o....",
    "...........",
    "...........",
    "...........",
    "....o4o....",
    "o222222222o",
    ".o5oo4oo5o.",
    "....o4o....",
    "....o4o....",
    "....o4o....",
    "....ooo....",
  ] },
  raised: { x: 3, y: 0, rows: [
    ".....o4o...",
    "o222222222o",
    ".o5oo4oo5o.",
    ".....o4o...",
    ".....o4o...",
    ".....o4o...",
    ".....o4o...",
    "...........",
    "...........",
    ".....o4o...",
    ".....ooo...",
  ] },
};

/** A felling axe: a wooden haft, a broad head on the outer side. */
const AXE = {
  rest: { x: 4, y: 17, rows: [
    "....o4o",
    "....o4o",
    ".......",
    ".......",
    ".......",
    "..ooo4o",
    ".o3224o",
    "o32224o",
    "o32224o",
    ".o3224o",
    "..ooo4o",
    "....o4o",
    "....o4o",
    "....ooo",
  ] },
  raised: { x: 3, y: 0, rows: [
    "..ooo4o",
    ".o3224o",
    "o32224o",
    "o32224o",
    ".o3224o",
    "..ooo4o",
    "....o4o",
    ".......",
    ".......",
    "....o4o",
    "....ooo",
  ] },
};

/** A pickaxe: a haft and a head pointed at both ends. */
const PICKAXE = {
  rest: { x: 4, y: 17, rows: [
    "....o4o....",
    "....o4o....",
    "...........",
    "...........",
    "...........",
    ".ooooooooo.",
    "o322222221o",
    ".oo.o4o.oo.",
    "....o4o....",
    "....o4o....",
    "....o4o....",
    "....o4o....",
    "....ooo....",
  ] },
  raised: { x: 3, y: 0, rows: [
    ".ooooooooo.",
    "o322222221o",
    ".oo.o4o.oo.",
    "....o4o....",
    "....o4o....",
    "....o4o....",
    "....o4o....",
    "...........",
    "...........",
    "....o4o....",
    "....ooo....",
  ] },
};

// --- the catalogue -------------------------------------------------------------

const LEATHER_TRIM = "#c9a86a";
const RUBY = "#d23b3b";

const helm = (id: string, tier: MetalTier): Item => ({ id, slot: "head", layer: FULL_HELM, colours: tones(METAL[tier]), over: "beard" });
const chestplate = (id: string, tier: MetalTier): Item => ({ id, slot: "body", layer: CHESTPLATE, shoulders: PAULDRONS, colours: tones(METAL[tier]), sleeves: METAL[tier] });
const greaves = (id: string, tier: MetalTier): Item => ({ id, slot: "legs", layer: LEGS, colours: tones(METAL[tier]) });
const heaterShield = (id: string, tier: MetalTier): Item => ({ id, slot: "shield", layer: HEATER_SHIELD, colours: tones(METAL[tier]) });
const sabre = (id: string, tier: MetalTier): Item => ({ id, slot: "weapon", weapon: SABRE, colours: tones(METAL[tier], GOLD) });
const boots = (id: string, base: Hex): Item => ({ id, slot: "feet", layer: FEET, colours: tones(base) });
const gloves = (id: string, base: Hex): Item => ({ id, slot: "hands", colours: {}, hands: base });
const staff = (id: string, frames: { rest: Frame; raised: Frame }, orb: Hex): Item => ({ id, slot: "weapon", weapon: frames, colours: tones(WOOD, orb) });
const bow = (id: string, frames: { rest: Frame; raised: Frame }, wood: Hex): Item => ({ id, slot: "weapon", weapon: frames, colours: { ...tones(wood), "5": STRING } });

/** The ladder tiers (progression spec 2026-10-24): the achievement tiers, Bronze to Divine. */
export const GEAR_TIERS = ["bronze", "silver", "gold", "platinum", "diamond", "obsidian", "divine"] as const;
export type GearTier = (typeof GEAR_TIERS)[number];

/** One item per gear tier, keyed `<tier>-<piece>`. */
function perTier<K extends string>(piece: K, make: (id: string, tier: GearTier) => Item) {
  return Object.fromEntries(GEAR_TIERS.map((t) => [`${t}-${piece}`, make(`${t}-${piece}`, t)])) as Record<`${GearTier}-${K}`, Item>;
}

/** A hide set: hood, body, leggings, bracers and boots of one material. */
function hideSet<M extends string>(m: M, base: Hex, trim: Hex, body: Frame) {
  return {
    [`${m}-hood`]: { id: `${m}-hood`, slot: "head", layer: HOOD, colours: tones(base), over: "hair" },
    [`${m}-body`]: { id: `${m}-body`, slot: "body", layer: body, colours: tones(base, trim) },
    [`${m}-leggings`]: { id: `${m}-leggings`, slot: "legs", layer: LEGS, colours: tones(base) },
    [`${m}-bracers`]: gloves(`${m}-bracers`, base),
    [`${m}-boots`]: boots(`${m}-boots`, dark(base)),
  } as Record<`${M}-${"hood" | "body" | "leggings" | "bracers" | "boots"}`, Item>;
}

/** A robe set: hat, robe, skirt and boots of one order. */
function robeSet<M extends string>(m: M, robe: { base: Hex; trim: Hex }, hat: Frame, skirt: Frame) {
  return {
    [`${m}-hat`]: { id: `${m}-hat`, slot: "head", layer: hat, colours: { ...tones(robe.base, robe.trim), "6": RUBY } },
    [`${m}-robe`]: { id: `${m}-robe`, slot: "body", layer: ROBE_TOP, colours: tones(robe.base, robe.trim), sleeves: robe.base },
    [`${m}-skirt`]: { id: `${m}-skirt`, slot: "legs", layer: skirt, colours: tones(robe.base, robe.trim) },
    [`${m}-boots`]: boots(`${m}-boots`, dark(robe.base)),
  } as Record<`${M}-${"hat" | "robe" | "skirt" | "boots"}`, Item>;
}

/** A mastery cloak and its trimmed form (two or more skills at 99), keyed by the skill it marks. */
const MASTERY_CLOAKS = Object.fromEntries(
  (Object.keys(MASTERY_CLOAK) as SkillId[]).flatMap((e): [string, Item][] => {
    const { base, trim } = MASTERY_CLOAK[e];
    const colours = { o: OUTLINE, c: base, C: dark(base), "5": trim };
    return [
      [`${e}-cloak`, { id: `${e}-cloak`, slot: "cape", layer: CAPE, drape: CAPE_DRAPE, colours }],
      [`${e}-cloak-t`, { id: `${e}-cloak-t`, slot: "cape", layer: TRIMMED_CAPE, drape: CAPE_DRAPE, colours }],
    ];
  }),
) as Record<`${SkillId}-cloak` | `${SkillId}-cloak-t`, Item>;

const BASE_ITEMS = {
  // warriors, one full set per metal tier
  "bronze-helm": helm("bronze-helm", "bronze"),
  "silver-helm": helm("silver-helm", "silver"),
  "gold-helm": helm("gold-helm", "gold"),
  "platinum-helm": helm("platinum-helm", "platinum"),
  "diamond-helm": helm("diamond-helm", "diamond"),
  "obsidian-helm": helm("obsidian-helm", "obsidian"),
  "crimson-helm": helm("crimson-helm", "crimson"),
  "bronze-chestplate": chestplate("bronze-chestplate", "bronze"),
  "silver-chestplate": chestplate("silver-chestplate", "silver"),
  "gold-chestplate": chestplate("gold-chestplate", "gold"),
  "platinum-chestplate": chestplate("platinum-chestplate", "platinum"),
  "diamond-chestplate": chestplate("diamond-chestplate", "diamond"),
  "obsidian-chestplate": chestplate("obsidian-chestplate", "obsidian"),
  "crimson-chestplate": chestplate("crimson-chestplate", "crimson"),
  "bronze-greaves": greaves("bronze-greaves", "bronze"),
  "silver-greaves": greaves("silver-greaves", "silver"),
  "gold-greaves": greaves("gold-greaves", "gold"),
  "platinum-greaves": greaves("platinum-greaves", "platinum"),
  "diamond-greaves": greaves("diamond-greaves", "diamond"),
  "obsidian-greaves": greaves("obsidian-greaves", "obsidian"),
  "crimson-greaves": greaves("crimson-greaves", "crimson"),
  "bronze-heater-shield": heaterShield("bronze-heater-shield", "bronze"),
  "silver-heater-shield": heaterShield("silver-heater-shield", "silver"),
  "gold-heater-shield": heaterShield("gold-heater-shield", "gold"),
  "platinum-heater-shield": heaterShield("platinum-heater-shield", "platinum"),
  "diamond-heater-shield": heaterShield("diamond-heater-shield", "diamond"),
  "obsidian-heater-shield": heaterShield("obsidian-heater-shield", "obsidian"),
  "crimson-heater-shield": heaterShield("crimson-heater-shield", "crimson"),
  "bronze-sabre": sabre("bronze-sabre", "bronze"),
  "silver-sabre": sabre("silver-sabre", "silver"),
  "gold-sabre": sabre("gold-sabre", "gold"),
  "platinum-sabre": sabre("platinum-sabre", "platinum"),
  "diamond-sabre": sabre("diamond-sabre", "diamond"),
  "obsidian-sabre": sabre("obsidian-sabre", "obsidian"),
  "crimson-sabre": sabre("crimson-sabre", "crimson"),
  "metal-boots": boots("metal-boots", METAL.silver),
  "black-boots": boots("black-boots", METAL.black),
  // the champion: black plate trimmed in gold, a two-hander
  "crested-helm": { id: "crested-helm", slot: "head", layer: CRESTED_HELM, colours: tones(METAL.black, GOLD), over: "beard" } satisfies Item,
  "trimmed-chestplate": { id: "trimmed-chestplate", slot: "body", layer: TRIMMED_CHESTPLATE, shoulders: TRIMMED_PAULDRONS, colours: tones(METAL.black, GOLD), sleeves: METAL.black } satisfies Item,
  "trimmed-greaves": { id: "trimmed-greaves", slot: "legs", layer: TRIMMED_LEGS, colours: tones(METAL.black, GOLD) } satisfies Item,
  "greatsword": { id: "greatsword", slot: "weapon", weapon: GREATSWORD, colours: tones(METAL.gold, GOLD) } satisfies Item,
  "gold-gauntlets": gloves("gold-gauntlets", GOLD),
  // mages
  "novice-hat": { id: "novice-hat", slot: "head", layer: WIZARD_HAT, colours: tones(MAGE.novice) } satisfies Item,
  "novice-robe": { id: "novice-robe", slot: "body", layer: ROBE_TOP, colours: tones(MAGE.novice, light(MAGE.novice)), sleeves: MAGE.novice } satisfies Item,
  "novice-skirt": { id: "novice-skirt", slot: "legs", layer: SKIRT, colours: tones(MAGE.novice) } satisfies Item,
  "novice-boots": boots("novice-boots", dark(MAGE.novice)),
  "staff": staff("staff", STAFF, "#9fc5ff"),
  "violet-hat": { id: "violet-hat", slot: "head", layer: BANDED_HAT, colours: tones(MAGE.violet, GOLD) } satisfies Item,
  "violet-robe": { id: "violet-robe", slot: "body", layer: ROBE_TOP, colours: tones(MAGE.violet, GOLD), sleeves: MAGE.violet } satisfies Item,
  "violet-skirt": { id: "violet-skirt", slot: "legs", layer: BANDED_SKIRT, colours: tones(MAGE.violet, GOLD) } satisfies Item,
  "violet-boots": boots("violet-boots", dark(MAGE.violet)),
  "orb-staff": staff("orb-staff", ORB_STAFF, "#c58cff"),
  "archmage-hat": { id: "archmage-hat", slot: "head", layer: GEMMED_HAT, colours: { ...tones(MAGE.high, GOLD), "6": RUBY } } satisfies Item,
  "archmage-robe": { id: "archmage-robe", slot: "body", layer: ROBE_TOP, colours: tones(MAGE.high, GOLD), sleeves: MAGE.high } satisfies Item,
  "archmage-skirt": { id: "archmage-skirt", slot: "legs", layer: RUNED_SKIRT, colours: tones(MAGE.high, GOLD) } satisfies Item,
  "archmage-boots": boots("archmage-boots", MAGE.high),
  "glass-staff": staff("glass-staff", GLASS_STAFF, "#7fe3ff"),
  // archers
  "leather-body": { id: "leather-body", slot: "body", layer: JERKIN, colours: tones(LEATHER.plain, LEATHER_TRIM) } satisfies Item,
  "leather-leggings": { id: "leather-leggings", slot: "legs", layer: LEGS, colours: tones(LEATHER.plain) } satisfies Item,
  "leather-bracers": gloves("leather-bracers", LEATHER.plain),
  "leather-boots": boots("leather-boots", dark(LEATHER.plain)),
  "shortbow": bow("shortbow", SHORTBOW, WOOD),
  "green-hood": { id: "green-hood", slot: "head", layer: HOOD, colours: tones(LEATHER.green), over: "hair" } satisfies Item,
  "green-body": { id: "green-body", slot: "body", layer: STRAPPED_BODY, colours: tones(LEATHER.green, LEATHER_TRIM) } satisfies Item,
  "green-leggings": { id: "green-leggings", slot: "legs", layer: LEGS, colours: tones(LEATHER.green) } satisfies Item,
  "green-bracers": gloves("green-bracers", LEATHER.green),
  "green-boots": boots("green-boots", dark(LEATHER.green)),
  "longbow": bow("longbow", LONGBOW, WOOD),
  "black-hood": { id: "black-hood", slot: "head", layer: HOOD, colours: tones(LEATHER.black), over: "hair" } satisfies Item,
  "black-body": { id: "black-body", slot: "body", layer: STRAPPED_BODY, colours: tones(LEATHER.black, LEATHER.red) } satisfies Item,
  "black-leggings": { id: "black-leggings", slot: "legs", layer: LEGS, colours: tones(LEATHER.black) } satisfies Item,
  "black-bracers": gloves("black-bracers", LEATHER.black),
  "night-bow": bow("night-bow", LONGBOW, METAL.black),
  "hunter-body": { id: "hunter-body", slot: "body", layer: STRAPPED_BODY, colours: tones(LEATHER.red, GOLD) } satisfies Item,
  "hunter-leggings": { id: "hunter-leggings", slot: "legs", layer: TRIMMED_LEGS, colours: tones(LEATHER.red, GOLD) } satisfies Item,
  "hunter-boots": boots("hunter-boots", dark(LEATHER.red)),
  "crossbow": { id: "crossbow", slot: "weapon", weapon: CROSSBOW, colours: { ...tones(METAL.gold, STRING), "4": WOOD } } satisfies Item,
  // extras
  "monk-robe": { id: "monk-robe", slot: "body", layer: ROBE_TOP, colours: tones("#8a5a2b", "#d9c08a"), sleeves: "#8a5a2b" } satisfies Item,
  "monk-skirt": { id: "monk-skirt", slot: "legs", layer: SKIRT, colours: tones("#8a5a2b") } satisfies Item,
  "pendant": { id: "pendant", slot: "neck", layer: PENDANT, colours: tones(GOLD, "#d9c08a") } satisfies Item,
  "tricorne": { id: "tricorne", slot: "head", layer: TRICORNE, colours: tones(METAL.black, GOLD) } satisfies Item,
  "striped-shirt": { id: "striped-shirt", slot: "body", layer: STRIPED_SHIRT, colours: tones("#9e2b2b", "#e8e2d0"), sleeves: "#9e2b2b" } satisfies Item,
  "cutlass": { id: "cutlass", slot: "weapon", weapon: SABRE, colours: tones(METAL.gold, "#8a5a2b") } satisfies Item,
  "axe": { id: "axe", slot: "weapon", weapon: AXE, colours: { ...tones(METAL.gold), "4": WOOD } } satisfies Item,
  "mining-helmet": { id: "mining-helmet", slot: "head", layer: MINING_HELMET, colours: { ...tones("#c9a227"), "6": "#fff3b0" }, over: "hair" } satisfies Item,
  "pickaxe": { id: "pickaxe", slot: "weapon", weapon: PICKAXE, colours: { ...tones(METAL.silver), "4": WOOD } } satisfies Item,
  // worn by anyone
  "amulet": { id: "amulet", slot: "neck", layer: AMULET, colours: { o: OUTLINE, "5": GOLD, r: RUBY } } satisfies Item,
  "cape": { id: "cape", slot: "cape", layer: CAPE, colours: { o: OUTLINE, c: CAPE_FILL, C: CAPE_SHADE } } satisfies Item,
  "trimmed-cape": { id: "trimmed-cape", slot: "cape", layer: TRIMMED_CAPE, colours: { o: OUTLINE, c: CAPE_FILL, C: CAPE_SHADE, "5": GOLD } } satisfies Item,
  // every quest done: the account's quest cloak, worn by its highest-level character
  "quest-cloak": { id: "quest-cloak", slot: "cape", layer: CAPE, drape: CAPE_DRAPE, colours: { o: OUTLINE, c: QUEST_CLOAK.base, C: dark(QUEST_CLOAK.base), "5": QUEST_CLOAK.trim } } satisfies Item,
  // the ladders (progression spec 2026-10-24): what a character wears for its tier
  "divine-helm": helm("divine-helm", "divine"),
  "divine-chestplate": chestplate("divine-chestplate", "divine"),
  "divine-greaves": greaves("divine-greaves", "divine"),
  "divine-heater-shield": heaterShield("divine-heater-shield", "divine"),
  "divine-sabre": sabre("divine-sabre", "divine"),
  "divine-boots": boots("divine-boots", METAL.divine),
  ...perTier("open-helm", (id, t) => ({ id, slot: "head", layer: OPEN_HELM, colours: tones(METAL[t]), over: "hair" })),
  ...perTier("axe", (id, t) => ({ id, slot: "weapon", weapon: AXE, colours: { ...tones(METAL[t]), "4": WOOD } })),
  ...perTier("pickaxe", (id, t) => ({ id, slot: "weapon", weapon: PICKAXE, colours: { ...tones(METAL[t]), "4": WOOD } })),
  "leather-hood": { id: "leather-hood", slot: "head", layer: HOOD, colours: tones(HIDE.leather), over: "hair" } satisfies Item,
  ...hideSet("hard", HIDE.hard, LEATHER_TRIM, JERKIN),
  ...hideSet("studded", HIDE.studded, METAL.gold, JERKIN),
  ...hideSet("blue", HIDE.blue, LEATHER_TRIM, STRAPPED_BODY),
  ...hideSet("red", HIDE.red, LEATHER_TRIM, STRAPPED_BODY),
  ...hideSet("pale", HIDE.pale, GOLD, STRAPPED_BODY),
  "ash-shortbow": bow("ash-shortbow", SHORTBOW, "#a8743a"),
  "hunting-bow": bow("hunting-bow", LONGBOW, "#b89a5a"),
  "war-bow": bow("war-bow", LONGBOW, "#5e3218"),
  "composite-bow": bow("composite-bow", LONGBOW, "#3d5a4a"),
  "radiant-bow": bow("radiant-bow", LONGBOW, "#a9dcf0"),
  ...robeSet("dark", ROBE.dark, WIZARD_HAT, SKIRT),
  ...robeSet("enchanted", ROBE.enchanted, BANDED_HAT, BANDED_SKIRT),
  ...robeSet("tidal", ROBE.tidal, BANDED_HAT, BANDED_SKIRT),
  ...robeSet("celestial", ROBE.celestial, GEMMED_HAT, RUNED_SKIRT),
  "silver-staff": staff("silver-staff", STAFF, "#e6e6ea"),
  "gold-staff": staff("gold-staff", STAFF, "#8fe39a"),
  "diamond-staff": staff("diamond-staff", ORB_STAFF, "#7fe3c0"),
  "divine-staff": staff("divine-staff", GLASS_STAFF, "#fff3b0"),
  // a mastery cloak replaces the party cape at 99
  ...MASTERY_CLOAKS,
  // rares: the paper crown wears the party colour, like the cape
  "paper-crown": { id: "paper-crown", slot: "head", layer: PAPER_CROWN, colours: { o: OUTLINE, "1": CAPE_SHADE, "2": CAPE_FILL, "3": "color-mix(in srgb, var(--sprite-fill) 60%, white)" } } satisfies Item,
  "golden-paper-crown": { id: "golden-paper-crown", slot: "head", layer: PAPER_CROWN, colours: tones("#f2f0ea") } satisfies Item,
  "harvest-mask": { id: "harvest-mask", slot: "head", layer: HARVEST_MASK, colours: tones("#3d9a3a"), over: "beard" } satisfies Item,
} as const satisfies Record<string, Item>;

type CapeId = "cape" | "trimmed-cape" | "quest-cloak" | `${SkillId}-cloak` | `${SkillId}-cloak-t`;

/** Divine Warden's reward (spec 2026-10-24): every cape, trimmed in the Divine metal. */
const DIVINE_CAPES = Object.fromEntries(
  (Object.values(BASE_ITEMS) as Item[])
    .filter((item) => item.slot === "cape")
    .map((item): [string, Item] => [
      `${item.id}-divine`,
      { ...item, id: `${item.id}-divine`, layer: TRIMMED_CAPE, colours: { ...item.colours, "5": METAL.divine } },
    ]),
) as Record<`${CapeId}-divine`, Item>;

export const ITEMS = { ...BASE_ITEMS, ...DIVINE_CAPES } as const satisfies Record<string, Item>;

export type ItemId = keyof typeof ITEMS;
