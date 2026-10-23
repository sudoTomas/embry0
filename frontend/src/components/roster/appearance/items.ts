/**
 * The equipment catalogue: everything an adventurer can wear, one shape per
 * kind of thing drawn once over the three-tone key of palette.ts, so a
 * helm is every metal tier of itself and a robe every order of mage. Every
 * shape is original pixel art. Positions follow the body kit of parts.ts:
 * the weapon hand is the left (viewer's left) one, down at x 8..10 rows
 * 19..21 and raised at x 7..9 rows 7..8; the shield arm is the right one.
 */
import { CAPE_FILL, CAPE_SHADE, GOLD, LEATHER, MAGE, METAL, OUTLINE, STRING, WOOD, dark, light, tones, type Hex, type MetalTier } from "./palette.ts";
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
const COIF: Frame = { x: 10, y: 1, rows: [
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

// --- body slot ---------------------------------------------------------------

/** A chest plate with a centre seam, a collar and a belt line. Its pauldrons go over the arms. */
const PLATEBODY: Frame = { x: 11, y: 11, rows: [
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
const TRIMMED_PLATEBODY: Frame = { x: 11, y: 11, rows: [
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

/** Platelegs with a trim belt and knee plates. */
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
/** The archmage's skirt: trim hem and a rune row. */
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

/** A kiteshield on the right arm: square shoulders tapering to a point, an emblem in the dark tone. */
const KITESHIELD: Frame = { x: 20, y: 12, rows: [
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

// --- weapons: rest in the down hand, raised above the up hand ----------------

/** A curved single-edged blade, widening toward the tip. */
const SCIMITAR = {
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
const CRYSTAL_STAFF = {
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
const platebody = (id: string, tier: MetalTier): Item => ({ id, slot: "body", layer: PLATEBODY, shoulders: PAULDRONS, colours: tones(METAL[tier]), sleeves: METAL[tier] });
const platelegs = (id: string, tier: MetalTier): Item => ({ id, slot: "legs", layer: LEGS, colours: tones(METAL[tier]) });
const kiteshield = (id: string, tier: MetalTier): Item => ({ id, slot: "shield", layer: KITESHIELD, colours: tones(METAL[tier]) });
const scimitar = (id: string, tier: MetalTier): Item => ({ id, slot: "weapon", weapon: SCIMITAR, colours: tones(METAL[tier], GOLD) });
const boots = (id: string, base: Hex): Item => ({ id, slot: "feet", layer: FEET, colours: tones(base) });
const gloves = (id: string, base: Hex): Item => ({ id, slot: "hands", colours: {}, hands: base });
const staff = (id: string, frames: { rest: Frame; raised: Frame }, orb: Hex): Item => ({ id, slot: "weapon", weapon: frames, colours: tones(WOOD, orb) });
const bow = (id: string, frames: { rest: Frame; raised: Frame }, wood: Hex): Item => ({ id, slot: "weapon", weapon: frames, colours: { ...tones(wood), "5": STRING } });

export const ITEMS = {
  // warriors, one full set per metal tier
  "bronze-helm": helm("bronze-helm", "bronze"),
  "iron-helm": helm("iron-helm", "iron"),
  "steel-helm": helm("steel-helm", "steel"),
  "mithril-helm": helm("mithril-helm", "mithril"),
  "adamant-helm": helm("adamant-helm", "adamant"),
  "rune-helm": helm("rune-helm", "rune"),
  "dragon-helm": helm("dragon-helm", "dragon"),
  "bronze-platebody": platebody("bronze-platebody", "bronze"),
  "iron-platebody": platebody("iron-platebody", "iron"),
  "steel-platebody": platebody("steel-platebody", "steel"),
  "mithril-platebody": platebody("mithril-platebody", "mithril"),
  "adamant-platebody": platebody("adamant-platebody", "adamant"),
  "rune-platebody": platebody("rune-platebody", "rune"),
  "dragon-platebody": platebody("dragon-platebody", "dragon"),
  "bronze-platelegs": platelegs("bronze-platelegs", "bronze"),
  "iron-platelegs": platelegs("iron-platelegs", "iron"),
  "steel-platelegs": platelegs("steel-platelegs", "steel"),
  "mithril-platelegs": platelegs("mithril-platelegs", "mithril"),
  "adamant-platelegs": platelegs("adamant-platelegs", "adamant"),
  "rune-platelegs": platelegs("rune-platelegs", "rune"),
  "dragon-platelegs": platelegs("dragon-platelegs", "dragon"),
  "bronze-kiteshield": kiteshield("bronze-kiteshield", "bronze"),
  "iron-kiteshield": kiteshield("iron-kiteshield", "iron"),
  "steel-kiteshield": kiteshield("steel-kiteshield", "steel"),
  "mithril-kiteshield": kiteshield("mithril-kiteshield", "mithril"),
  "adamant-kiteshield": kiteshield("adamant-kiteshield", "adamant"),
  "rune-kiteshield": kiteshield("rune-kiteshield", "rune"),
  "dragon-kiteshield": kiteshield("dragon-kiteshield", "dragon"),
  "bronze-scimitar": scimitar("bronze-scimitar", "bronze"),
  "iron-scimitar": scimitar("iron-scimitar", "iron"),
  "steel-scimitar": scimitar("steel-scimitar", "steel"),
  "mithril-scimitar": scimitar("mithril-scimitar", "mithril"),
  "adamant-scimitar": scimitar("adamant-scimitar", "adamant"),
  "rune-scimitar": scimitar("rune-scimitar", "rune"),
  "dragon-scimitar": scimitar("dragon-scimitar", "dragon"),
  "metal-boots": boots("metal-boots", METAL.iron),
  "black-boots": boots("black-boots", METAL.black),
  // the champion: black plate trimmed in gold, a two-hander
  "crested-helm": { id: "crested-helm", slot: "head", layer: CRESTED_HELM, colours: tones(METAL.black, GOLD), over: "beard" } satisfies Item,
  "trimmed-platebody": { id: "trimmed-platebody", slot: "body", layer: TRIMMED_PLATEBODY, shoulders: TRIMMED_PAULDRONS, colours: tones(METAL.black, GOLD), sleeves: METAL.black } satisfies Item,
  "trimmed-platelegs": { id: "trimmed-platelegs", slot: "legs", layer: TRIMMED_LEGS, colours: tones(METAL.black, GOLD) } satisfies Item,
  "greatsword": { id: "greatsword", slot: "weapon", weapon: GREATSWORD, colours: tones(METAL.steel, GOLD) } satisfies Item,
  "gold-gauntlets": gloves("gold-gauntlets", GOLD),
  // mages
  "wizard-hat": { id: "wizard-hat", slot: "head", layer: WIZARD_HAT, colours: tones(MAGE.wizard) } satisfies Item,
  "wizard-robe": { id: "wizard-robe", slot: "body", layer: ROBE_TOP, colours: tones(MAGE.wizard, light(MAGE.wizard)), sleeves: MAGE.wizard } satisfies Item,
  "wizard-skirt": { id: "wizard-skirt", slot: "legs", layer: SKIRT, colours: tones(MAGE.wizard) } satisfies Item,
  "wizard-boots": boots("wizard-boots", dark(MAGE.wizard)),
  "staff": staff("staff", STAFF, "#9fc5ff"),
  "mystic-hat": { id: "mystic-hat", slot: "head", layer: BANDED_HAT, colours: tones(MAGE.mystic, GOLD) } satisfies Item,
  "mystic-robe": { id: "mystic-robe", slot: "body", layer: ROBE_TOP, colours: tones(MAGE.mystic, GOLD), sleeves: MAGE.mystic } satisfies Item,
  "mystic-skirt": { id: "mystic-skirt", slot: "legs", layer: BANDED_SKIRT, colours: tones(MAGE.mystic, GOLD) } satisfies Item,
  "mystic-boots": boots("mystic-boots", dark(MAGE.mystic)),
  "orb-staff": staff("orb-staff", ORB_STAFF, "#c58cff"),
  "archmage-hat": { id: "archmage-hat", slot: "head", layer: GEMMED_HAT, colours: { ...tones(MAGE.high, GOLD), "6": RUBY } } satisfies Item,
  "archmage-robe": { id: "archmage-robe", slot: "body", layer: ROBE_TOP, colours: tones(MAGE.high, GOLD), sleeves: MAGE.high } satisfies Item,
  "archmage-skirt": { id: "archmage-skirt", slot: "legs", layer: RUNED_SKIRT, colours: tones(MAGE.high, GOLD) } satisfies Item,
  "archmage-boots": boots("archmage-boots", MAGE.high),
  "crystal-staff": staff("crystal-staff", CRYSTAL_STAFF, "#7fe3ff"),
  // archers
  "leather-body": { id: "leather-body", slot: "body", layer: JERKIN, colours: tones(LEATHER.plain, LEATHER_TRIM) } satisfies Item,
  "leather-chaps": { id: "leather-chaps", slot: "legs", layer: LEGS, colours: tones(LEATHER.plain) } satisfies Item,
  "leather-vambraces": gloves("leather-vambraces", LEATHER.plain),
  "leather-boots": boots("leather-boots", dark(LEATHER.plain)),
  "shortbow": bow("shortbow", SHORTBOW, WOOD),
  "green-coif": { id: "green-coif", slot: "head", layer: COIF, colours: tones(LEATHER.green), over: "hair" } satisfies Item,
  "green-body": { id: "green-body", slot: "body", layer: STRAPPED_BODY, colours: tones(LEATHER.green, LEATHER_TRIM) } satisfies Item,
  "green-chaps": { id: "green-chaps", slot: "legs", layer: LEGS, colours: tones(LEATHER.green) } satisfies Item,
  "green-vambraces": gloves("green-vambraces", LEATHER.green),
  "green-boots": boots("green-boots", dark(LEATHER.green)),
  "longbow": bow("longbow", LONGBOW, WOOD),
  "black-coif": { id: "black-coif", slot: "head", layer: COIF, colours: tones(LEATHER.black), over: "hair" } satisfies Item,
  "black-body": { id: "black-body", slot: "body", layer: STRAPPED_BODY, colours: tones(LEATHER.black, LEATHER.red) } satisfies Item,
  "black-chaps": { id: "black-chaps", slot: "legs", layer: LEGS, colours: tones(LEATHER.black) } satisfies Item,
  "black-vambraces": gloves("black-vambraces", LEATHER.black),
  "dark-bow": bow("dark-bow", LONGBOW, METAL.black),
  "hunter-body": { id: "hunter-body", slot: "body", layer: STRAPPED_BODY, colours: tones(LEATHER.red, GOLD) } satisfies Item,
  "hunter-chaps": { id: "hunter-chaps", slot: "legs", layer: TRIMMED_LEGS, colours: tones(LEATHER.red, GOLD) } satisfies Item,
  "hunter-boots": boots("hunter-boots", dark(LEATHER.red)),
  "crossbow": { id: "crossbow", slot: "weapon", weapon: CROSSBOW, colours: { ...tones(METAL.steel, STRING), "4": WOOD } } satisfies Item,
  // extras
  "monk-robe": { id: "monk-robe", slot: "body", layer: ROBE_TOP, colours: tones("#8a5a2b", "#d9c08a"), sleeves: "#8a5a2b" } satisfies Item,
  "monk-skirt": { id: "monk-skirt", slot: "legs", layer: SKIRT, colours: tones("#8a5a2b") } satisfies Item,
  "pendant": { id: "pendant", slot: "neck", layer: PENDANT, colours: tones(GOLD, "#d9c08a") } satisfies Item,
  "tricorne": { id: "tricorne", slot: "head", layer: TRICORNE, colours: tones(METAL.black, GOLD) } satisfies Item,
  "striped-shirt": { id: "striped-shirt", slot: "body", layer: STRIPED_SHIRT, colours: tones("#9e2b2b", "#e8e2d0"), sleeves: "#9e2b2b" } satisfies Item,
  "cutlass": { id: "cutlass", slot: "weapon", weapon: SCIMITAR, colours: tones(METAL.steel, "#8a5a2b") } satisfies Item,
  "axe": { id: "axe", slot: "weapon", weapon: AXE, colours: { ...tones(METAL.steel), "4": WOOD } } satisfies Item,
  "mining-helmet": { id: "mining-helmet", slot: "head", layer: MINING_HELMET, colours: { ...tones("#c9a227"), "6": "#fff3b0" }, over: "hair" } satisfies Item,
  "pickaxe": { id: "pickaxe", slot: "weapon", weapon: PICKAXE, colours: { ...tones(METAL.iron), "4": WOOD } } satisfies Item,
  // worn by anyone
  "amulet": { id: "amulet", slot: "neck", layer: AMULET, colours: { o: OUTLINE, "5": GOLD, r: RUBY } } satisfies Item,
  "cape": { id: "cape", slot: "cape", layer: CAPE, colours: { o: OUTLINE, c: CAPE_FILL, C: CAPE_SHADE } } satisfies Item,
  "trimmed-cape": { id: "trimmed-cape", slot: "cape", layer: TRIMMED_CAPE, colours: { o: OUTLINE, c: CAPE_FILL, C: CAPE_SHADE, "5": GOLD } } satisfies Item,
} as const satisfies Record<string, Item>;

export type ItemId = keyof typeof ITEMS;
