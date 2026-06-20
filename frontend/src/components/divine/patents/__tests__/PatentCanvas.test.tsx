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
        inventor="O. BODART"
        patentNo="EMBRY0-001"
        title="Geodesic Identity"
        elements="FIRE · WATER"
        epigraph="As above, so below"
      >
        <span>inside</span>
      </PatentCanvas>,
    );
    expect(screen.getByText("MAY 04, 2026")).toBeInTheDocument();
    expect(screen.getByText("O. BODART")).toBeInTheDocument();
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

  it("respects size prop", () => {
    const { container } = render(<FullGeodesicSphere size={300} />);
    expect(container.querySelector("svg")?.getAttribute("width")).toBe("300");
  });
});
