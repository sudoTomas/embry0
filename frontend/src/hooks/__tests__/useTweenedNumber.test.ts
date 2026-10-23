import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { TWEEN_MS, useTweenedNumber } from "../useTweenedNumber";

/** Motion allowed: matchMedia answers "no preference". */
function allowMotion() {
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
}

/** requestAnimationFrame under the test's hand: run(now) plays one frame. */
function stubFrames() {
  let queued = new Map<number, FrameRequestCallback>();
  let next = 0;
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    queued.set(++next, cb);
    return next;
  });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => queued.delete(id));
  return {
    pending: () => queued.size,
    run(now: number) {
      const due = [...queued.values()];
      queued = new Map();
      act(() => due.forEach((cb) => cb(now)));
    },
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useTweenedNumber", () => {
  it("is the target at once under reduced motion, first value and later ones", () => {
    const { result, rerender } = renderHook(({ n }) => useTweenedNumber(n, { countUp: true }), {
      initialProps: { n: undefined as number | undefined },
    });
    expect(result.current).toBeUndefined();
    rerender({ n: 42 });
    expect(result.current).toBe(42);
    rerender({ n: 7 });
    expect(result.current).toBe(7);
  });

  it("counts a first value up from 0, reaches the target and stops", () => {
    allowMotion();
    const frames = stubFrames();
    const { result } = renderHook(() => useTweenedNumber(80, { countUp: true }));
    expect(result.current).toBe(0);

    frames.run(1000);
    frames.run(1000 + TWEEN_MS / 2);
    expect(result.current).toBeGreaterThan(0);
    expect(result.current).toBeLessThan(80);

    frames.run(1000 + TWEEN_MS);
    expect(result.current).toBe(80);
    expect(frames.pending()).toBe(0);
  });

  it("shows a first value at once without countUp, then travels to a later one and ticks once", () => {
    allowMotion();
    const frames = stubFrames();
    const el = document.createElement("b");
    const animate = vi.fn();
    (el as unknown as { animate: typeof animate }).animate = animate;
    const tickRef = { current: el };

    const { result, rerender } = renderHook(({ n }) => useTweenedNumber(n, { tickRef }), {
      initialProps: { n: 4 },
    });
    expect(result.current).toBe(4);
    expect(animate).not.toHaveBeenCalled();

    rerender({ n: 5 });
    expect(result.current).toBe(4);
    expect(animate).toHaveBeenCalledTimes(1);
    frames.run(0);
    frames.run(TWEEN_MS);
    expect(result.current).toBe(5);
    expect(frames.pending()).toBe(0);
    expect(animate).toHaveBeenCalledTimes(1);
  });

  it("starts a new target from where an unfinished tween got to", () => {
    allowMotion();
    const frames = stubFrames();
    const { result, rerender } = renderHook(({ n }) => useTweenedNumber(n), { initialProps: { n: 0 } });
    rerender({ n: 100 });
    frames.run(0);
    frames.run(TWEEN_MS / 4);
    const midway = result.current as number;
    expect(midway).toBeGreaterThan(0);

    rerender({ n: 10 });
    expect(result.current).toBe(midway);
    frames.run(1000);
    frames.run(1000 + TWEEN_MS);
    expect(result.current).toBe(10);
    expect(frames.pending()).toBe(0);
  });
});
