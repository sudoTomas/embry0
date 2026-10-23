import type { CSSProperties } from "react";
import { prefersReducedMotion } from "@/hooks/useReducedMotion";

/**
 * The turn of an element among siblings arriving together: index.css's
 * `.motion-enter` delays it by `--i` steps (capped at 12).
 */
export function motionIndex(i: number): CSSProperties {
  return { "--i": i } as CSSProperties;
}

/** index.css --ease-standard, for the Web Animations API (it takes no var()). */
export const EASE_STANDARD = "cubic-bezier(0.16, 1, 0.3, 1)";
/** index.css --ease-travel. */
export const EASE_TRAVEL = "cubic-bezier(0.65, 0, 0.35, 1)";

/**
 * A one-shot bump on an element whose value just changed: up to `scale` and
 * back. The rise slows into the peak at 40% of the run and the settle eases
 * out of it, each on its own easing: one strong ease-out over the whole run
 * peaked at about 30 ms and read as a flicker. Nothing happens under reduced
 * motion or without the Web Animations API.
 */
export function tick(el: Element | null, scale = 1.18): void {
  if (!el || prefersReducedMotion() || typeof (el as HTMLElement).animate !== "function") return;
  (el as HTMLElement).animate(
    [
      { transform: "scale(1)", easing: "ease-out" },
      { transform: `scale(${scale})`, offset: 0.4, easing: "ease-in-out" },
      { transform: "scale(1)" },
    ],
    { duration: 280 },
  );
}

/**
 * A motion token from index.css in ms, read where a script needs it, or
 * `fallback` without styles (jsdom). The built stylesheet writes "1400ms" as
 * "1.4s", so the unit decides.
 */
export function motionMs(el: Element, token: string, fallback: number): number {
  const value = getComputedStyle(el).getPropertyValue(token).trim();
  const n = parseFloat(value);
  if (!Number.isFinite(n)) return fallback;
  return value.endsWith("ms") ? n : value.endsWith("s") ? n * 1000 : n;
}
