import { afterEach, describe, expect, it, vi } from "vitest";
import { motionIndex, tick } from "../motion";

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
});
