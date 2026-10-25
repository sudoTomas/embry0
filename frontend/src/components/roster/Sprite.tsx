import type { CSSProperties } from "react";
import { DEFAULT_AVATAR, DEFAULT_COLOR, isAvatarId, isPaletteId, type AvatarId } from "@/lib/agentIdentity";
import { cn } from "@/lib/utils";
import { COMPOSED, GRID, PRESETS, STYLE, composeState, dress, outfitKey, walkFrames, type ComposedFrame, type Outfit } from "./appearance";
import type { SpriteState } from "./sprites";
import "./roster.css";

export interface SpriteProps {
  /** A roster id (spec 3.10); anything else renders golem. */
  avatar: string;
  /** A palette id; anything else renders slate. */
  color: string;
  state: SpriteState;
  size?: 16 | 32 | 48 | 64;
  /** The name in the aria-label "<name>, <state in words>"; defaults to the avatar id. Pass the display name. */
  label?: string;
  /** Hidden from assistive tech, for a sprite inside a control that already carries the name. */
  decorative?: boolean;
  /** Draw frame 0 only, never stepping: for a sprite repeated down a list, like a transcript marker. */
  still?: boolean;
  /** What the character earned (progression spec 2026-10-24): its tier's gear, a skill cape, a rare. None draws the avatar as it is. */
  outfit?: Outfit | null;
  /** Walking (the world, spec 2026-10-25): its two walk frames in place of the state's. */
  walking?: boolean;
  className?: string;
}

/** How each state reads aloud: the one place a sprite state becomes words. */
const STATE_WORDS: Record<SpriteState, string> = {
  idle: "idle", thinking: "thinking", tool: "using a tool", done: "done", failed: "failed", pending: "waiting",
};

/** Dressed frames, by avatar, outfit and state: a roster of the same characters composes each once. */
const DRESSED = new Map<string, ComposedFrame[]>();

function framesOf(avatar: AvatarId, state: SpriteState, outfit: Outfit | null | undefined, walking = false): ComposedFrame[] {
  if (!outfit && !walking) return COMPOSED[avatar][state];
  const key = `${avatar}|${outfit ? outfitKey(outfit) : "-"}|${walking ? "walk" : state}`;
  let frames = DRESSED.get(key);
  if (!frames) {
    const appearance = outfit ? dress(PRESETS[avatar], STYLE[avatar], outfit) : PRESETS[avatar];
    frames = walking ? walkFrames(appearance) : composeState(appearance, state);
    DRESSED.set(key, frames);
  }
  return frames;
}

/** One avatar in one state: a crisp 32x32 SVG, its cape tinted by the palette token, stepping between two frames by CSS. */
export function Sprite({ avatar, color, state, size = 32, label, decorative = false, still = false, outfit, walking = false, className }: SpriteProps) {
  const av = isAvatarId(avatar) ? avatar : DEFAULT_AVATAR;
  const col = isPaletteId(color) ? color : DEFAULT_COLOR;
  const all = framesOf(av, state, outfit, walking);
  const frames = still ? all.slice(0, 1) : all;
  return (
    <svg
      {...(decorative ? { "aria-hidden": true } : { role: "img", "aria-label": `${label ?? av}, ${STATE_WORDS[state]}` })}
      viewBox={`0 0 ${GRID} ${GRID}`}
      width={size}
      height={size}
      shapeRendering="crispEdges"
      className={cn("sprite shrink-0", className)}
      data-avatar={av}
      data-color={col}
      data-state={state}
      data-frames={frames.length}
      {...(outfit ? { "data-tier": outfit.tier } : {})}
      style={{ "--sprite-fill": `var(--color-palette-${col})` } as CSSProperties}
    >
      {frames.map((frame, i) => (
        <g key={i} className="sprite-frame" data-frame={i}>
          {Object.entries(frame.paths).map(([fill, d]) => (
            <path key={fill} d={d} fill={fill} />
          ))}
        </g>
      ))}
    </svg>
  );
}
