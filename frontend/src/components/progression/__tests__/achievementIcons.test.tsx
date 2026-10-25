import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ACHIEVEMENT_ICONS, lift } from "../achievementIconData";
import { AchievementIcon } from "../achievementIcons";

describe("achievement icons (spec 2026-10-25)", () => {
  it("draws every icon as 12×12 pixels in the colour it is given", () => {
    expect(ACHIEVEMENT_ICONS.length).toBeGreaterThanOrEqual(30);
    for (const name of ACHIEVEMENT_ICONS) {
      const { container, unmount } = render(<AchievementIcon name={name} color="#9c6b2f" />);
      const svg = container.querySelector("svg") as SVGElement;
      expect(svg.getAttribute("viewBox")).toBe("0 0 12 12");
      const rects = [...svg.querySelectorAll("rect")];
      expect(rects.length, name).toBeGreaterThan(10);
      for (const r of rects) {
        expect(Number(r.getAttribute("x")), name).toBeLessThan(12);
        expect(Number(r.getAttribute("y")), name).toBeLessThan(12);
      }
      expect(rects.some((r) => r.getAttribute("fill") === lift("#9c6b2f")), name).toBe(true);
      unmount();
    }
  });

  it("lifts a metal a little towards white, and leaves a CSS variable alone", () => {
    expect(lift("#000000")).toBe("#2e2e2e");
    expect(lift("#2f4ea0")).toBe("#546eb1");
    expect(lift("var(--color-palette-gold)")).toBe("var(--color-palette-gold)");
  });

  it("draws a question mark for a secret and the star for an unknown name", () => {
    const { container } = render(<AchievementIcon name={null} color="#fff" title="A secret" />);
    expect(container.querySelector("svg")).toHaveAttribute("data-icon", "secret");
    expect(container.querySelector("svg")).toHaveAttribute("aria-label", "A secret");
    const unknown = render(<AchievementIcon name="no-such" color="#fff" />).container.querySelector("svg");
    expect(unknown?.querySelectorAll("rect").length).toBe(
      render(<AchievementIcon name="star" color="#fff" />).container.querySelector("svg")?.querySelectorAll("rect").length,
    );
  });
});
