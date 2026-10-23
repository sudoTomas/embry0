/**
 * The body kit: the layers every adventurer is built from before anything
 * is worn. A tall, slim, slightly stiff figure seen from the front, big
 * hands and feet, on the 32 by 32 grid of types.ts: hair on rows 1 to 4,
 * head 2 to 10, torso 11 to 19, belt and legs 20 to 28, feet 29 to 31.
 * The arms are the one part a pose moves (down, up, slumped); the right
 * arm is the left one mirrored.
 *
 * Colour keys: `o` outline, `s`/`S` skin and its shade, `e` eye, `m`
 * mouth, `h`/`H` hair and its shade, `a`/`A` sleeve, `g`/`G` hand,
 * `1`/`2` dark and base of a clothing colour.
 */
import { COLOURS, OUTLINE, SKIN, dark, light, mix, type Hex } from "./palette.ts";
import {
  EMPTY_FRAME, GRID,
  type ArmPose, type BeardStyle, type ColourId, type Face, type Frame, type HairStyle, type Layer, type SkinTone,
} from "./types.ts";

/** The frame flipped left to right across the grid's centre line. */
export function mirror(f: Frame): Frame {
  const width = Math.max(0, ...f.rows.map((r) => r.length));
  return {
    x: GRID - f.x - width,
    y: f.y,
    rows: f.rows.map((r) => [...r.padEnd(width, ".")].reverse().join("")),
  };
}

// --- head: face x 11..20, rows 2..9, neck row 10; eyes on row 5, mouth on rows 7..8 ---

const HEAD_TOP = [
  ".oooooooo.",
  "osssssssSo",
  "osssssssSo",
];
const EYES: Record<"open" | "closed", string> = {
  open: "osesssseSo",
  closed: "osSsssSSSo",
};
const NOSE = "osssSsssSo";
const MOUTH: Record<Face, [string, string]> = {
  open: ["osssssssSo", "osssmmssSo"],
  closed: ["osssssssSo", "osssmmssSo"],
  smile: ["ossmssmsSo", "osssmmssSo"],
  frown: ["osssmmssSo", "ossmssmsSo"],
};
const HEAD_BOTTOM = [
  ".oSSSSSSo.",
  "...osSo...",
];

export function head(skin: SkinTone, face: Face): Layer {
  const eyes = EYES[face === "closed" ? "closed" : "open"];
  const rows = [...HEAD_TOP, eyes, NOSE, ...MOUTH[face], ...HEAD_BOTTOM];
  const base = SKIN[skin];
  return {
    frame: { x: 11, y: 2, rows },
    colours: { o: OUTLINE, s: base, S: dark(base), e: "#1a1420", m: mix(base, "#000000", 0.55) },
  };
}

// --- hair: over the head's top rows; `long` and `curly` fall down the sides ---

export const HAIR_STYLES: Record<HairStyle, Frame> = {
  short: { x: 11, y: 1, rows: [
    ".oooooooo.",
    "ohhhhhhhHo",
    "ohhhhhhhHo",
    "oh......Ho",
  ] },
  spiky: { x: 11, y: 1, rows: [
    "oh.ohho.Ho",
    "ohhhhhhhHo",
    "ohhhhhhhHo",
  ] },
  long: { x: 10, y: 1, rows: [
    "..oooooooo..",
    ".ohhhhhhhHo.",
    "ohhhhhhhhhHo",
    "ohh......HHo",
    "oh........Ho",
    "oh........Ho",
    "oh........Ho",
    "oh........Ho",
    "oh........Ho",
    "oh........Ho",
    "oh........Ho",
    ".o........o.",
  ] },
  mohawk: { x: 11, y: 1, rows: [
    "...ohHo...",
    "...ohHo...",
  ] },
  bald: EMPTY_FRAME,
  curly: { x: 10, y: 1, rows: [
    "..ohhoohho..",
    ".ohhhhhhhHHo",
    "ohhhhhhhhhHo",
    "ohh......HHo",
    ".o........o.",
  ] },
  fringe: { x: 11, y: 1, rows: [
    ".oooooooo.",
    "ohhhhhhhHo",
    "ohhhhhhhHo",
    "ohhhhho.Ho",
    "oH......Ho",
  ] },
};

export function hair(style: HairStyle, colour: ColourId): Layer {
  const base = COLOURS[colour];
  return { frame: HAIR_STYLES[style], colours: { o: OUTLINE, h: base, H: dark(base) } };
}

// --- beards: over the lower face, in the hair colour ---

export const BEARDS: Record<BeardStyle, Frame> = {
  goatee: { x: 14, y: 8, rows: [
    ".hh.",
    "ohHo",
    ".oo.",
  ] },
  full: { x: 11, y: 7, rows: [
    "oh......Ho",
    "ohhhhhhhHo",
    "ohhhhhhhHo",
    ".ohhhhhHo.",
    "..oooooo..",
  ] },
  moustache: { x: 13, y: 7, rows: [
    "hHhhHh",
  ] },
  long: { x: 11, y: 7, rows: [
    "oh......Ho",
    "ohhhhhhhHo",
    "ohhhhhhhHo",
    ".ohhhhhHo.",
    ".ohhhhhHo.",
    "..ohhhHo..",
    "..ohhhHo..",
    "...oooo...",
  ] },
};

export function beard(style: BeardStyle, colour: ColourId): Layer {
  const base = COLOURS[colour];
  return { frame: BEARDS[style], colours: { o: OUTLINE, h: base, H: dark(base) } };
}

// --- torso, legs and feet: a plain shirt, trousers with a belt, boots ---

export const TORSO: Frame = { x: 11, y: 11, rows: [
  "oooooooooo",
  "o32222221o",
  "o32222221o",
  "o32222221o",
  "o32222221o",
  "o32222221o",
  "o32222221o",
  "o32222221o",
  "o32222221o",
] };

export const LEGS: Frame = { x: 11, y: 20, rows: [
  "o11111111o",
  "o322oo221o",
  "o322oo221o",
  "o322oo221o",
  "o322oo221o",
  "o322oo221o",
  "o322oo221o",
  "o322oo221o",
  "o322oo221o",
] };

export const FEET: Frame = { x: 10, y: 29, rows: [
  "o3222oo2221o",
  "o1111oo1111o",
  "oooooooooooo",
] };

function clothing(frame: Frame, colour: ColourId): Layer {
  const base = COLOURS[colour];
  return { frame, colours: { o: OUTLINE, "1": dark(base), "2": base, "3": light(base) } };
}

export const torso = (colour: ColourId): Layer => clothing(TORSO, colour);
export const legs = (colour: ColourId): Layer => clothing(LEGS, colour);
export const feet = (colour: ColourId): Layer => clothing(FEET, colour);

// --- arms: the left (viewer's left) arm in each pose; the right arm is its mirror ---

export const ARMS: Record<ArmPose, Frame> = {
  down: { x: 7, y: 11, rows: [
    ".ooo",
    ".oaa",
    ".oaa",
    ".oaa",
    ".oaa",
    ".oaa",
    ".oaa",
    ".oAA",
    "oggg",
    "oggg",
    "oGGG",
    ".ooo",
  ] },
  up: { x: 6, y: 6, rows: [
    ".ooo.",
    "ogggo",
    "ogggo",
    "oGGGo",
    "oAaAo",
    "oaaao",
    "oaaaa",
    "oAaaa",
    ".oooo",
  ] },
  slump: { x: 6, y: 11, rows: [
    "..ooo",
    "..oaa",
    "..oaa",
    "..oaa",
    ".oaaA",
    ".oaa.",
    ".oaa.",
    ".oAA.",
    "oggg.",
    "oggg.",
    "oggg.",
    "oGGG.",
    ".ooo.",
  ] },
};

export function arm(side: "left" | "right", pose: ArmPose, sleeve: Hex, hand: Hex): Layer {
  const frame = side === "left" ? ARMS[pose] : mirror(ARMS[pose]);
  return {
    frame,
    colours: { o: OUTLINE, a: sleeve, A: dark(sleeve), g: hand, G: dark(hand) },
  };
}
