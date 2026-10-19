import { useEffect, useRef, useState, type RefObject } from "react";
import { tick } from "@/lib/motion";
import { useReducedMotion } from "./useReducedMotion";

/** How long a number takes to reach a new value: about index.css's
 * --motion-slow, long enough to read as counting and short enough that the
 * value is there before anyone waits for it. */
export const TWEEN_MS = 600;

/** Fast at first, then settling: most of the way there in the first third. */
const easeOut = (t: number) => 1 - (1 - t) ** 3;

interface TweenOptions {
  /** The first value counts up from 0, as an Overview tile does when it
   * loads. Without it the first value shows at once and only later changes
   * travel. */
  countUp?: boolean;
  /** The element that shows the number: it gets tick() once when a later
   * value arrives, never on the first. */
  tickRef?: RefObject<Element | null>;
}

/**
 * The number to draw this frame for a live value: it travels from what is on
 * screen to `target` on requestAnimationFrame and the frames stop once it
 * gets there. Under reduced motion, or in a hidden tab where frames do not
 * run, it is `target` at once. Mid-way it is fractional, so a count rounds
 * it in its format.
 */
export function useTweenedNumber(
  target: number | undefined,
  { countUp = false, tickRef }: TweenOptions = {},
): number | undefined {
  const reduced = useReducedMotion();
  // What is on screen, undefined until a first value arrives. The ref
  // mirrors it so a new target starts from wherever the last one got to.
  const [shown, setShown] = useState<number | undefined>(undefined);
  const shownRef = useRef<number | undefined>(undefined);

  useEffect(() => {
    if (target === undefined) return;
    const last = shownRef.current;
    const from = last ?? (countUp ? 0 : target);
    const show = (value: number) => {
      shownRef.current = value;
      setShown(value);
    };
    if (reduced || from === target || document.hidden) {
      show(target);
      return;
    }
    if (last !== undefined) tick(tickRef?.current ?? null);
    let start: number | undefined;
    let frame = requestAnimationFrame(function step(now) {
      start ??= now;
      const t = Math.min(1, (now - start) / TWEEN_MS);
      show(t === 1 ? target : from + (target - from) * easeOut(t));
      if (t < 1) frame = requestAnimationFrame(step);
    });
    return () => cancelAnimationFrame(frame);
  }, [target, reduced, countUp, tickRef]);

  if (target === undefined) return undefined;
  if (reduced) return target;
  return shown ?? (countUp ? 0 : target);
}
