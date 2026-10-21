import { afterEach, describe, expect, it, vi } from "vitest";
import { motionIndex, motionMs, tick } from "../motion";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("motion", () => {
  it("sets the arrival turn as --i", () => {
    expect(motionIndex(3)).toEqual({ "--i": 3 });
  });

  it("leaves an element still when motion is reduced", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
    const el = document.createElement("span");
    const animate = vi.fn();
    (el as unknown as { animate: typeof animate }).animate = animate;
    tick(el);
    expect(animate).not.toHaveBeenCalled();
  });

  it("bumps an element once when motion is allowed", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    const el = document.createElement("span");
    const animate = vi.fn();
    (el as unknown as { animate: typeof animate }).animate = animate;
    tick(el);
    expect(animate).toHaveBeenCalledTimes(1);
  });

  it("peaks the bump at 40 percent of its run, rise and settle eased on their own", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    const el = document.createElement("span");
    const animate = vi.fn();
    (el as unknown as { animate: typeof animate }).animate = animate;
    tick(el, 1.2);
    // No easing over the whole run: one there would bend the keyframe
    // offsets and move the peak again.
    expect(animate).toHaveBeenCalledWith(
      [
        { transform: "scale(1)", easing: "ease-out" },
        { transform: "scale(1.2)", offset: 0.4, easing: "ease-in-out" },
        { transform: "scale(1)" },
      ],
      { duration: 280 },
    );
  });

  it("reads a motion token in seconds or ms, as the built stylesheet writes it", () => {
    const el = document.createElement("div");
    document.body.appendChild(el);
    el.style.setProperty("--motion-draw", "1.4s");
    el.style.setProperty("--motion-step", "35ms");
    expect(motionMs(el, "--motion-draw", 0)).toBe(1400);
    expect(motionMs(el, "--motion-step", 0)).toBe(35);
    expect(motionMs(el, "--motion-none", 560)).toBe(560);
    el.remove();
  });
});
