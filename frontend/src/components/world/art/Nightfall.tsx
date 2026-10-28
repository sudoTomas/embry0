import type { CSSProperties } from "react";
import { NIGHTFALL_FILTER_ID, nightMatrixValues } from "./relight";

/** The night filter, drawn once on a page that shows a scene. */
export function NightfallFilter() {
  return (
    <svg width="0" height="0" aria-hidden="true" focusable="false" style={{ position: "absolute", width: 0, height: 0 }}>
      <filter id={NIGHTFALL_FILTER_ID} colorInterpolationFilters="sRGB">
        <feColorMatrix type="matrix" values={nightMatrixValues()} />
      </filter>
    </svg>
  );
}

/**
 * A pool of gold light on (x, y) in a scene: torchlight, added to what is
 * under it (world.css). It falls where the work is: the hall's torches, the
 * quest board, loot waiting for a look, a party out on a quest.
 */
export function Torchlight({ x, y, radius, style }: { x: number; y: number; radius: number; style?: CSSProperties }) {
  return (
    <span
      aria-hidden="true"
      className="torchlight"
      data-testid="torchlight"
      style={{ left: Math.round(x - radius), top: Math.round(y - radius), width: 2 * radius, height: 2 * radius, ...style }}
    />
  );
}
