import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { PatentCanvas } from "../PatentCanvas";
import { PatentFigure } from "../PatentFigure";
import { FullGeodesicSphere } from "../FullGeodesicSphere";

describe("PatentCanvas", () => {
  it("renders title, header (date/inventor/patentNo), and footer (elements/epigraph)", () => {
    render(
      <PatentCanvas
        date="MAY 04, 2026"
        inventor="EMBRY0"
        patentNo="EMBRY0-001"
        title="Geodesic Identity"
        elements="FIRE · WATER"
        epigraph="As above, so below"
      >
        <span>inside</span>
      </PatentCanvas>,
    );
    expect(screen.getByText("MAY 04, 2026")).toBeInTheDocument();
    expect(screen.getByText("EMBRY0")).toBeInTheDocument();
    expect(screen.getByText("EMBRY0-001")).toBeInTheDocument();
    expect(screen.getByText("Geodesic Identity")).toBeInTheDocument();
    expect(screen.getByText("FIRE · WATER")).toBeInTheDocument();
    expect(screen.getByText("As above, so below")).toBeInTheDocument();
    expect(screen.getByText("inside")).toBeInTheDocument();
  });

  it("omits header when no header props are passed", () => {
    render(<PatentCanvas><span>x</span></PatentCanvas>);
    expect(screen.queryByText(/2026/)).not.toBeInTheDocument();
  });

  it("draws its frame and rules in the neutral border, not gold", () => {
    // Gold marks only the one thing on a view that needs a person.
    const { container } = render(
      <PatentCanvas date="MAY 04, 2026" title="T" epigraph="E">
        <span>x</span>
      </PatentCanvas>,
    );
    const bordered = Array.from(container.querySelectorAll("[class*='border']"));
    expect(bordered).toHaveLength(3);
    for (const el of bordered) {
      expect(el.className).not.toMatch(/(^|\s)border(-[trblxy])?-primary/);
    }
  });

  it("lays its chrome on the surface token in the neutral tones, not a hard-coded colour", () => {
    // Rule 2 of the divine layer: colour comes from the theme tokens.
    const { container } = render(<PatentCanvas title="T"><span>x</span></PatentCanvas>);
    const canvas = container.firstElementChild as HTMLElement;
    expect(canvas.className).toContain("bg-surface");
    expect(canvas.className).toContain("text-fg-secondary");
    expect(canvas.getAttribute("style")).toBeNull();
  });

  it("carries divine-element class for the escape hatch", () => {
    const { container } = render(<PatentCanvas><span>x</span></PatentCanvas>);
    expect(container.querySelector(".divine-element")).not.toBeNull();
  });
});

describe("PatentFigure", () => {
  it("renders the FIG. <number> — <caption>", () => {
    render(
      <PatentFigure number="I" caption="Geodesic">
        <svg />
      </PatentFigure>,
    );
    expect(screen.getByText(/FIG\. I — Geodesic/)).toBeInTheDocument();
  });

  it("omits the em-dash and caption when only number is given", () => {
    render(<PatentFigure number="II"><svg /></PatentFigure>);
    expect(screen.getByText(/FIG\. II/)).toBeInTheDocument();
    expect(screen.queryByText(/—/)).not.toBeInTheDocument();
  });
});

describe("FullGeodesicSphere", () => {
  it("renders an SVG with cardinal dots and equator", () => {
    const { container } = render(<FullGeodesicSphere />);
    const svg = container.querySelector("svg");
    expect(svg).not.toBeNull();
    // 4 cardinal dots at the patent-scale radius
    const dots = container.querySelectorAll('svg circle[r="3"]');
    expect(dots).toHaveLength(4);
    // Outer ring at scaled radius 78
    expect(container.querySelector('svg circle[r="78"]')).not.toBeNull();
  });

  it("sets no colour of its own, so the figure around it gives the tone", () => {
    const { container } = render(<FullGeodesicSphere />);
    expect(container.querySelector("svg")?.getAttribute("class")).not.toMatch(/(^|\s)text-/);
  });

  it("respects size prop", () => {
    const { container } = render(<FullGeodesicSphere size={300} />);
    expect(container.querySelector("svg")?.getAttribute("width")).toBe("300");
  });
});
