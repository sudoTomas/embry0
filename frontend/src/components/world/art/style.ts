import type { CSSProperties } from "react";
import type { Rect } from "./manifest";

/** The style that stands a box of ``w`` by ``h`` with its anchor on map point (x, y), drawn in front by its foot. */
export function anchored(x: number, y: number, anchor: { x: number; y: number }): CSSProperties {
  return { position: "absolute", left: Math.round(x - anchor.x), top: Math.round(y - anchor.y), zIndex: Math.round(y) };
}

/** The keyframes already in the document, by name. */
const INJECTED = new Set<string>();

/**
 * Keyframes that step a background through ``frames`` (a creature's or an
 * effect's frames may wrap across rows of its sheet, so each frame is named),
 * added to the document once; returns their name.
 */
export function frameKeyframes(name: string, frames: readonly Rect[]): string {
  const id = `art-${name.replace(/[^a-z0-9_-]/gi, "-")}`;
  if (INJECTED.has(id) || typeof document === "undefined") return id;
  const n = frames.length;
  const steps = frames.map((f, i) => `${((i / n) * 100).toFixed(3)}% { background-position: -${f[0]}px -${f[1]}px; }`).join(" ");
  const last = frames[n - 1];
  const style = document.createElement("style");
  style.dataset.art = id;
  style.textContent = `@keyframes ${id} { ${steps} 100% { background-position: -${last[0]}px -${last[1]}px; } }`;
  document.head.appendChild(style);
  INJECTED.add(id);
  return id;
}
