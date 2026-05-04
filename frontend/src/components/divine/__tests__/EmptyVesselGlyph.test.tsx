import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { EmptyVesselGlyph } from "../EmptyVesselGlyph";

describe("EmptyVesselGlyph", () => {
  it("renders the minimal-density mark (no struts, no hemispheres)", () => {
    const { container } = render(<EmptyVesselGlyph copy="anything" />);
    expect(container.querySelector('svg circle[r="22"]')).not.toBeNull();
    expect(container.querySelectorAll('svg circle[r="2.4"]')).toHaveLength(4);
    expect(container.querySelector('svg path[d^="M 32 18"]')).toBeNull();
  });

  it("renders the supplied copy", () => {
    render(<EmptyVesselGlyph copy="The vessel is empty." />);
    expect(screen.getByText("The vessel is empty.")).toBeInTheDocument();
  });

  it("renders the optional sub-copy when provided", () => {
    render(
      <EmptyVesselGlyph
        copy="The vessel is empty."
        subCopy="Drop a repository to begin the work."
      />
    );
    expect(
      screen.getByText("Drop a repository to begin the work.")
    ).toBeInTheDocument();
  });

  it("carries the divine-element class", () => {
    const { container } = render(<EmptyVesselGlyph copy="x" />);
    expect(container.querySelector(".divine-element")).not.toBeNull();
  });

  it("renders the static minimal mark when no operation prop is passed (backward compat)", () => {
    const { container } = render(<EmptyVesselGlyph copy="x" />);
    // No operation animation classes should be present
    expect(container.querySelector(".divine-op-calcinate-struts")).toBeNull();
    // Static ring is still rendered
    expect(container.querySelector('svg circle[r="22"]')).not.toBeNull();
  });

  it("delegates to DivineOperation when operation prop is provided", () => {
    const { container } = render(
      <EmptyVesselGlyph copy="The matter awaits" operation="calcinate" />
    );
    expect(container.querySelector(".divine-op-calcinate-struts")).not.toBeNull();
  });

  it("operation prop selects the right animation class per operation", () => {
    const { container: cDissolve } = render(
      <EmptyVesselGlyph copy="x" operation="dissolve" />
    );
    expect(cDissolve.querySelector(".divine-op-dissolve-ring")).not.toBeNull();

    const { container: cCoag } = render(
      <EmptyVesselGlyph copy="x" operation="coagulate" />
    );
    expect(cCoag.querySelector(".divine-op-coagulate-fill")).not.toBeNull();
  });
});
