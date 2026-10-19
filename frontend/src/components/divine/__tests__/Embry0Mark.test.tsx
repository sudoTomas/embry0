import { afterEach, describe, it, expect, vi } from "vitest";

// Browsers have AnimationEvent and jsdom does not, so without one react-dom
// listens for the prefixed webkitAnimationEnd and a plain animationend never
// reaches onAnimationEnd. Give jsdom the browser's event before react-dom
// loads, so the test sends what a browser sends.
vi.hoisted(() => {
  if (!("AnimationEvent" in globalThis)) {
    class AnimationEvent extends Event {
      readonly animationName: string;
      constructor(type: string, init: AnimationEventInit = {}) {
        super(type, init);
        this.animationName = init.animationName ?? "";
      }
    }
    Object.assign(globalThis, { AnimationEvent });
  }
});

import { fireEvent, render, screen } from "@testing-library/react";
import { Embry0Mark } from "../Embry0Mark";

/**
 * The brand master, embry0-mark.svg, verbatim: the gold line and the cream
 * dot. The header draws this drawing; the browser tab draws the pixel-grid
 * redraw in public/favicon.svg, which the brand set limits to 16 to 48 px.
 */
const MASTER_LINE =
  "M31.47 6.02c3.35-.21 6.88 1.12 9.39 3.31 2.78 2.42 4.48 5.86 4.71 9.54.09 1.49.02 3.49.02 5.02v17.2c0 1.94.12 4.22-.28 6.1-.48 2.37-1.59 4.56-3.22 6.36-2.52 2.81-5.79 4.23-9.53 4.44-3.61.1-6.84-.98-9.57-3.38-2.7-2.38-4.34-5.74-4.55-9.34-.09-1.51-.04-3.35-.04-4.89V23.47c0-2.07-.1-4.73.32-6.68.51-2.33 1.62-4.49 3.21-6.26 2.53-2.83 5.8-4.29 9.54-4.51zM34.59 46.02A2.55 2.55 0 0 0 37.08 42.71L37.35 42.63 37.47 43.03c.39 1.26.2 2.66-.41 3.81-.68 1.28-1.84 2.23-3.22 2.64-3.71 1.08-7.41-1.45-8.36-5.05-.58-2.19-.22-4.34.92-6.28 1.3-2.19 3.43-3.78 5.9-4.42 2.64-.66 5.43-.23 7.74 1.19.87.52 1.48 1.06 2.2 1.78-.03-3.79-.03-7.58-.02-11.36.01-2.18.17-6.27-.33-8.23-.49-1.85-1.47-3.53-2.85-4.86-1.92-1.87-4.5-2.9-7.18-2.86-2.73.06-5.32 1.2-7.2 3.17-1.35 1.42-2.27 3.18-2.67 5.1-.28 1.35-.2 3.16-.2 4.58V39.13c0 1.82-.05 3.89 0 5.69.09 2.7 1.25 5.26 3.24 7.1 1.98 1.84 4.61 2.82 7.31 2.73 2.74-.09 5.33-1.27 7.19-3.27 3.24-3.5 3.78-9.19.1-12.58-1.41-1.31-3.28-2-5.2-1.92-1.69.09-3.28.84-4.41 2.1-1.72 1.89-2.08 5.04-.04 6.83.81.7 1.86 1.05 2.93.98.49-.03 1.01-.14 1.45-.37L34.58 46.3z";
const MASTER_DOT = "M31.81 43.47a2.84 2.84 0 1 0 5.68 0 2.84 2.84 0 1 0-5.68 0z";

/** The master's line width, in its 64-unit box. */
const L = 3.4;
/** The x of the zero's four vertical walls in the master, outer and inner. */
const WALLS = [18.4, 21.79, 42.21, 45.59];

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

  it("draws the brand master: one line shape and one dot, cropped to the ink", () => {
    const { container } = render(<Embry0Mark />);
    const { svg, line, dot, rest } = markOf(container);
    expect(svg.getAttribute("viewBox")).toBe("18.4 6 27.2 52");
    expect(svg.getAttribute("width")).toBe("16");
    expect(svg.getAttribute("height")).toBe("31");
    expect(rest).toHaveLength(0);
    expect(svg.querySelectorAll("circle, ellipse, rect, line, polyline, polygon, g")).toHaveLength(0);
    expect(line.getAttribute("d")).toBe(MASTER_LINE);
    expect(dot.getAttribute("d")).toBe(MASTER_DOT);
  });

  it("draws the line 2 px wide with the zero's walls on whole pixels", () => {
    const { container } = render(<Embry0Mark />);
    const { svg } = markOf(container);
    const [x0, , w, h] = (svg.getAttribute("viewBox") ?? "").split(" ").map(Number);
    // preserveAspectRatio meet: the smaller of the two scales wins.
    const scale = Math.min(Number(svg.getAttribute("width")) / w, Number(svg.getAttribute("height")) / h);
    expect(L * scale).toBeCloseTo(2, 6);
    for (const x of WALLS) {
      const px = (x - x0) * scale;
      expect(Math.abs(px - Math.round(px)), `wall at ${x} lands at ${px} px`).toBeLessThan(0.01);
    }
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

/** The line's centre, the path the draw's stroke follows. */
const CENTRE_LINE =
  "M43.9 44.4V19.6A11.9 11.9 0 0 0 20.1 19.6V44.4A11.9 11.9 0 0 0 43.9 44.4A9.25 9.25 0 0 0 34.65 35.15A7.75 7.75 0 0 0 26.9 42.9A5.45 5.45 0 0 0 32.35 48.35A3.55 3.55 0 0 0 35.42 43.03";

/** jsdom has no matchMedia, which reads as reduced motion; this says no. */
function allowMotion() {
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} })),
  );
}

function pathWith(svg: SVGSVGElement, d: string) {
  const path = Array.from(svg.querySelectorAll("path")).find((p) => p.getAttribute("d") === d);
  if (!path) throw new Error(`no path draws ${d.slice(0, 12)}...`);
  return path;
}

function svgOf(container: HTMLElement) {
  const svg = container.querySelector("svg");
  if (!svg) throw new Error("Embry0Mark rendered no svg");
  return svg;
}

describe("Embry0Mark — the line draws itself once", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    delete document.body.dataset.divine;
  });

  it("stays still unless asked to draw", () => {
    allowMotion();
    const { container } = render(<Embry0Mark />);
    expect(svgOf(container).querySelector("mask")).toBeNull();
  });

  it("reveals the line through a mask that a stroke along its centre draws", () => {
    allowMotion();
    const { container } = render(<Embry0Mark draw />);
    const svg = svgOf(container);
    const mask = svg.querySelector("mask");
    expect(mask, "a drawing mark masks its line").not.toBeNull();
    expect(mask?.getAttribute("maskUnits")).toBe("userSpaceOnUse");
    expect([mask?.getAttribute("x"), mask?.getAttribute("y")]).toEqual(["18", "5"]);
    expect([mask?.getAttribute("width"), mask?.getAttribute("height")]).toEqual(["28", "54"]);
    const pen = pathWith(svg, CENTRE_LINE);
    expect(mask).toContainElement(pen);
    expect(pen.getAttribute("pathLength")).toBe("1");
    expect(pen.getAttribute("stroke-dasharray")).toBe("1");
    expect(pen.getAttribute("stroke-width")).toBe("4.6");
    expect(pen.getAttribute("class")).toBe("motion-mark-draw");
    expect(pathWith(svg, MASTER_LINE).getAttribute("mask")).toBe(`url(#${mask?.id})`);
    // The dot waits for the line, then arrives.
    expect(pathWith(svg, MASTER_DOT).getAttribute("class")).toBe("text-cream motion-mark-dot");
  });

  it("rests as the master, node for node, once the dot has arrived", () => {
    allowMotion();
    const still = svgOf(render(<Embry0Mark />).container).outerHTML;
    const { container } = render(<Embry0Mark draw />);
    fireEvent.animationEnd(pathWith(svgOf(container), MASTER_DOT));
    expect(svgOf(container).outerHTML).toBe(still);
  });

  it("shows the mark at rest at once under reduced motion", () => {
    const still = svgOf(render(<Embry0Mark />).container).outerHTML;
    const { container } = render(<Embry0Mark draw />);
    expect(svgOf(container).outerHTML).toBe(still);
  });

  it("shows the mark at rest at once when the divine layer is off", () => {
    allowMotion();
    document.body.dataset.divine = "off";
    const { container } = render(<Embry0Mark draw />);
    expect(svgOf(container).querySelector("mask")).toBeNull();
  });
});
