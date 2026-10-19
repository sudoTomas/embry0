import { render, screen } from "@testing-library/react";
import { describe, it, expect } from "vitest";
import favicon from "../../../../public/favicon.svg?raw";
import { Embry0Mark } from "../Embry0Mark";

/** The <path d> values of an SVG document, in order. */
function pathData(svgText: string): string[] {
  const doc = new DOMParser().parseFromString(svgText, "image/svg+xml");
  return [...doc.querySelectorAll("path")].map((p) => p.getAttribute("d") ?? "");
}

function markOf(container: HTMLElement) {
  const svg = container.querySelector("svg");
  if (!svg) throw new Error("Embry0Mark rendered no svg");
  const [line, dot, ...rest] = svg.querySelectorAll("path");
  return { svg, line, dot, rest };
}

describe("Embry0Mark", () => {
  it("renders the embry0 wordmark", () => {
    render(<Embry0Mark />);
    expect(screen.getByRole("img", { name: "embry0" })).toBeInTheDocument();
  });

  it("sets the 0 in the gold accent and the rest in the primary text colour", () => {
    render(<Embry0Mark />);
    expect(screen.getByText("embry").className).toContain("text-fg");
    expect(screen.getByText("0").className).toContain("text-primary");
  });

  it("draws the one-line mark: one line shape and one dot, cropped to the ink", () => {
    const { container } = render(<Embry0Mark />);
    const { svg, line, dot, rest } = markOf(container);
    expect(svg.getAttribute("viewBox")).toBe("16 0 32 64");
    expect(svg.getAttribute("height")).toBe("32");
    expect(svg.getAttribute("width")).toBe("16");
    expect(rest).toHaveLength(0);
    expect(svg.querySelectorAll("circle, ellipse, rect, line, polyline, polygon, g")).toHaveLength(0);
    // The line is one closed outline: the zero, then the curl and tail cut out of it.
    expect(line.getAttribute("d")).toMatch(/^M48 16V48A16 16 0 0 1 16 48V16/);
    // The dot is a circle of radius 4.25 centred at (36, 48), drawn as two arcs.
    expect(dot.getAttribute("d")).toBe("M31.75 48a4.25 4.25 0 1 0 8.5 0 4.25 4.25 0 1 0-8.5 0z");
  });

  it("is the same drawing as the browser tab icon", () => {
    const { container } = render(<Embry0Mark />);
    const { line, dot } = markOf(container);
    expect([line.getAttribute("d"), dot.getAttribute("d")]).toEqual(pathData(favicon));
  });

  it("draws a gold line and a cream dot by default, both through currentColor", () => {
    const { container } = render(<Embry0Mark />);
    const { svg, line, dot } = markOf(container);
    expect(line.getAttribute("fill")).toBe("currentColor");
    expect(dot.getAttribute("fill")).toBe("currentColor");
    expect(svg.getAttribute("class")).toContain("text-primary");
    expect(dot.getAttribute("class")).toContain("text-cream");
  });

  it("takes the caller's colour for the line, the dot and the word when mono", () => {
    const { container } = render(<Embry0Mark mono className="text-fg-muted" />);
    const { svg, line, dot } = markOf(container);
    expect(screen.getByRole("img", { name: "embry0" }).className).toContain("text-fg-muted");
    expect(line.getAttribute("fill")).toBe("currentColor");
    expect(dot.getAttribute("fill")).toBe("currentColor");
    expect(svg.getAttribute("class")).not.toContain("text-primary");
    expect(dot.getAttribute("class")).toBeNull();
    expect(screen.getByText("embry").className).toBe("");
    expect(screen.getByText("0").className).toBe("");
  });

  it("stays in place when the divine layer is off", () => {
    // body[data-divine="off"] hides every .divine-element (index.css); the
    // lockup is the product's name and must not vanish with the layer.
    const { container } = render(<Embry0Mark />);
    expect(container.querySelector(".divine-element")).toBeNull();
  });
});
