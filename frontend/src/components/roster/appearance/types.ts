/**
 * An adventurer is an appearance, not a drawing: a body kit (skin, hair,
 * beard and four colour slots) plus what is worn in nine equipment slots.
 * compose.ts renders one by layering the body-kit parts (parts.ts) and the
 * worn items (items.ts) on a 32 by 32 cell grid, the way an old-school MMO
 * draws a player from its appearance record. The twelve legacy avatars in
 * ../sprites.ts are drawn, not composed, and keep their own module.
 */
import type { Hex } from "./palette.ts";
import type { ItemId } from "./items.ts";

/** Cells per side of a composed frame. The character stands about 14 wide and 30 tall; row 0 is free for the bob and the spark. */
export const GRID = 32;

export type SkinTone = "light" | "tan" | "brown" | "dark";
export type HairStyle = "short" | "spiky" | "long" | "mohawk" | "bald" | "curly" | "fringe";
export type BeardStyle = "goatee" | "full" | "moustache" | "long";
export type ColourId =
  | "black" | "brown" | "blonde" | "auburn" | "grey" | "white"
  | "cream" | "umber" | "green" | "blue" | "red" | "purple" | "charcoal" | "teal";

export type EquipmentSlot = "head" | "cape" | "neck" | "weapon" | "body" | "shield" | "legs" | "hands" | "feet";

export interface Appearance {
  skin: SkinTone;
  hair: HairStyle;
  beard: BeardStyle | null;
  colours: { hair: ColourId; torso: ColourId; legs: ColourId; feet: ColourId };
  equipment: Partial<Record<EquipmentSlot, ItemId>>;
}

/**
 * A text frame placed on the grid: `rows[j][i]` is the cell at
 * (x + i, y + j); `.` is transparent, any other character is looked up in
 * the layer's colour key. Frames are cropped to what they draw, so a boot
 * is three rows, not thirty-two.
 */
export interface Frame {
  x: number;
  y: number;
  rows: readonly string[];
}

/** Character to colour for one frame. */
export type ColourKey = Record<string, Hex>;

export interface Layer {
  frame: Frame;
  colours: ColourKey;
}

/** The arm layers a pose can ask for; everything else stays put. */
export type ArmPose = "down" | "up" | "slump";
/** The face a pose can ask for; eyes closed is the thinking look. */
export type Face = "open" | "closed" | "smile" | "frown";

export const EMPTY_FRAME: Frame = { x: 0, y: 0, rows: [] };
