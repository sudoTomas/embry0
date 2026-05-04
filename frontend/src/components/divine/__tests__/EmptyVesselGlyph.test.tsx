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
});
