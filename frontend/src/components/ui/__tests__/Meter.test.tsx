import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { Meter, levelOf } from "../Meter";

function meter(value: number, max: number) {
  const { container } = render(<Meter value={value} max={max} label="Budget" />);
  const el = container.querySelector<HTMLElement>("[role='meter']")!;
  const bar = el.firstElementChild as HTMLElement;
  return { el, bar };
}

describe("Meter", () => {
  it("is a meter carrying its bounds and value", () => {
    const { el } = meter(2.5, 10);
    expect(el.getAttribute("aria-label")).toBe("Budget");
    expect(el.getAttribute("aria-valuemin")).toBe("0");
    expect(el.getAttribute("aria-valuemax")).toBe("10");
    expect(el.getAttribute("aria-valuenow")).toBe("2.5");
    expect(el.dataset.level).toBe("ok");
  });

  it("turns amber at 80 percent and red at 100", () => {
    expect(levelOf(7.99, 10)).toBe("ok");
    expect(levelOf(8, 10)).toBe("warn");
    expect(levelOf(10, 10)).toBe("over");
    expect(meter(8, 10).bar).toHaveClass("bg-palette-amber");
    expect(meter(10, 10).bar).toHaveClass("bg-destructive");
    expect(meter(1, 10).bar).toHaveClass("bg-palette-emerald");
  });

  it("caps the bar at full and never divides by zero", () => {
    const over = meter(12.5, 10);
    expect(over.el.dataset.level).toBe("over");
    expect(over.bar.style.width, "over budget still draws a full bar, not a wider one").toBe("100%");
    expect(over.el.getAttribute("aria-valuetext"), "a valuenow past valuemax needs words a reader can trust").toBe(
      "12.5 of 10, over",
    );
    expect(meter(8, 10).el.getAttribute("aria-valuetext")).toBe("8 of 10");
    const empty = meter(3, 0);
    expect(empty.bar.style.width).toBe("0%");
    expect(empty.el.dataset.level).toBe("ok");
    expect(empty.el.getAttribute("aria-valuemax")).toBe("0");
  });
});
