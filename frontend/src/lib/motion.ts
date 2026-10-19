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
 * back. Nothing happens under reduced motion or without the Web Animations
 * API.
 */
export function tick(el: Element | null, scale = 1.18): void {
  if (!el || prefersReducedMotion() || typeof (el as HTMLElement).animate !== "function") return;
  (el as HTMLElement).animate(
    [{ transform: "scale(1)" }, { transform: `scale(${scale})` }, { transform: "scale(1)" }],
    { duration: 280, easing: EASE_STANDARD },
  );
}
