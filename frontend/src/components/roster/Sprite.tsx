import type { CSSProperties } from "react";
import { DEFAULT_AVATAR, DEFAULT_COLOR, isAvatarId, isPaletteId } from "@/lib/agentIdentity";
import { cn } from "@/lib/utils";
import { COMPILED, type CharClass, type SpriteState } from "./sprites";
import "./roster.css";

export interface SpriteProps {
  /** A roster id; anything else renders golem (spec 3.10). */
  avatar: string;
  /** A palette id; anything else renders slate. */
  color: string;
  state: SpriteState;
  size?: 16 | 32 | 48 | 64;
  /** aria-label; defaults to "<avatar>, <state>". Pass the display name. */
  label?: string;
  className?: string;
}

const FILL: Record<CharClass, string> = {
  o: "var(--color-background)",
  f: "var(--sprite-fill)",
  h: "color-mix(in srgb, var(--sprite-fill) 70%, white)",
  e: "var(--color-foreground)",
};
const ORDER: CharClass[] = ["o", "f", "h", "e"];

/** One avatar in one state: a 16x16 SVG, tinted by the palette token, stepping between two frames by CSS. */
export function Sprite({ avatar, color, state, size = 32, label, className }: SpriteProps) {
  const av = isAvatarId(avatar) ? avatar : DEFAULT_AVATAR;
  const col = isPaletteId(color) ? color : DEFAULT_COLOR;
  const frames = COMPILED[av][state];
  return (
    <svg
      role="img"
      aria-label={label ?? `${av}, ${state}`}
      viewBox="0 0 16 16"
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
          {ORDER.filter((k) => paths[k]).map((k) => (
            <path key={k} d={paths[k]} fill={FILL[k]} />
          ))}
        </g>
      ))}
    </svg>
  );
}
