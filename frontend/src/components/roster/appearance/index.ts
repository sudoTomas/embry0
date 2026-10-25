export { GRID, type Appearance, type ArmPose, type BeardStyle, type ColourId, type EquipmentSlot, type Face, type Frame, type HairStyle, type Layer, type SkinTone } from "./types.ts";
export { ITEMS, type Item, type ItemId } from "./items.ts";
export { PRESETS, PRESET_IDS, type PresetId } from "./presets.ts";
export { GHOST_FILL, POSES, compose, composeState, layersOf, paint, runLength, walkFrames, type ComposedFrame, type Pose } from "./compose.ts";
export { LADDERS, RARES, STYLE, dress, outfitKey, type CombatStyle, type Outfit, type Rare } from "./outfit.ts";

import type { SpriteState } from "../sprites.ts";
import { composeState, type ComposedFrame } from "./compose.ts";
import { PRESETS, type PresetId } from "./presets.ts";
import { SPRITE_STATES } from "../sprites.ts";

/** Every preset in every state as it is drawn, composed once at module load; Sprite reads this for an avatar wearing no outfit. */
export const COMPOSED: Record<PresetId, Record<SpriteState, ComposedFrame[]>> = Object.fromEntries(
  (Object.keys(PRESETS) as PresetId[]).map((id) => [
    id,
    Object.fromEntries(SPRITE_STATES.map((state) => [state, composeState(PRESETS[id], state)])) as Record<SpriteState, ComposedFrame[]>,
  ]),
) as Record<PresetId, Record<SpriteState, ComposedFrame[]>>;
