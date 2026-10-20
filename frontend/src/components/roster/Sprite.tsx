import type { CSSProperties } from "react";
import { DEFAULT_AVATAR, DEFAULT_COLOR, isAdventurerId, isAvatarId, isPaletteId } from "@/lib/agentIdentity";
import { cn } from "@/lib/utils";
import { COMPOSED, GHOST_FILL, GRID } from "./appearance";
import { COMPILED, type CharClass, type SpriteState } from "./sprites";
import "./roster.css";

export interface SpriteProps {
  /** A roster id, legacy or adventurer preset; anything else renders golem (spec 3.10). */
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
  className?: string;
}

const FILL: Record<CharClass, string> = {
  // A dark shade of the palette colour, not the page background: the
  // silhouette holds on every surface and the figure reads as one colour.
  o: "color-mix(in srgb, var(--sprite-fill) 30%, black)",
  f: "var(--sprite-fill)",
  h: "color-mix(in srgb, var(--sprite-fill) 70%, white)",
  e: "var(--color-background)",
};
/** The pending frame is outline only; in the background colour it vanishes on every surface, so it is a dim tint instead (shared with the composed frames). */
const PENDING_OUTLINE = GHOST_FILL;

function fillFor(cls: CharClass, state: SpriteState): string {
  return cls === "o" && state === "pending" ? PENDING_OUTLINE : FILL[cls];
}
const ORDER: CharClass[] = ["o", "f", "h", "e"];

/** How each state reads aloud: the one place a sprite state becomes words. */
const STATE_WORDS: Record<SpriteState, string> = {
  idle: "idle", thinking: "thinking", tool: "using a tool", done: "done", failed: "failed", pending: "waiting",
};

/** The frames of one avatar in one state: the composed 32x32 ones of an adventurer preset, else the compiled 16x16 ones. */
type Painted = { grid: number; paths: { fill: string; d: string }[][] };

function framesOf(avatar: string, state: SpriteState): Painted {
  if (isAdventurerId(avatar)) {
    return { grid: GRID, paths: COMPOSED[avatar][state].map((f) => Object.entries(f.paths).map(([fill, d]) => ({ fill, d }))) };
  }
  const av = isAvatarId(avatar) ? avatar : DEFAULT_AVATAR;
  const legacy = COMPILED[av as keyof typeof COMPILED][state];
  return { grid: 16, paths: legacy.map((paths) => ORDER.filter((k) => paths[k]).map((k) => ({ fill: fillFor(k, state), d: paths[k]! }))) };
}

/** One avatar in one state: a crisp SVG tinted by the palette token, stepping between two frames by CSS. */
export function Sprite({ avatar, color, state, size = 32, label, decorative = false, still = false, className }: SpriteProps) {
  const av = isAvatarId(avatar) ? avatar : DEFAULT_AVATAR;
  const col = isPaletteId(color) ? color : DEFAULT_COLOR;
  const painted = framesOf(av, state);
  const frames = still ? painted.paths.slice(0, 1) : painted.paths;
  return (
    <svg
      {...(decorative ? { "aria-hidden": true } : { role: "img", "aria-label": `${label ?? av}, ${STATE_WORDS[state]}` })}
      viewBox={`0 0 ${painted.grid} ${painted.grid}`}
      width={size}
      height={size}
      shapeRendering="crispEdges"
      className={cn("sprite shrink-0", className)}
      data-avatar={av}
      data-color={col}
      data-state={state}
      data-frames={frames.length}
      style={{ "--sprite-fill": `var(--color-palette-${col})` } as CSSProperties}
    >
      {frames.map((paths, i) => (
        <g key={i} className="sprite-frame" data-frame={i}>
          {paths.map(({ fill, d }) => (
            <path key={fill} d={d} fill={fill} />
          ))}
        </g>
      ))}
    </svg>
  );
}
