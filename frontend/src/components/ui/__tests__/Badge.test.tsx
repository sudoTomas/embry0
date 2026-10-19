import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Badge } from "../Badge";

function allowMotion() {
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
}

const ring = () => document.querySelector('[data-slot="ring"]');

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("Badge", () => {
  it("renders its children", () => {
    render(<Badge>passed</Badge>);
    expect(screen.getByText("passed")).toBeInTheDocument();
  });

  it("applies the success tone classes", () => {
    render(<Badge tone="success">ok</Badge>);
    const el = screen.getByText("ok");
    expect(el.className).toContain("text-success");
    expect(el.className).toContain("bg-teal-dim");
  });

  it("falls back to neutral tone when no tone is provided", () => {
    render(<Badge>x</Badge>);
    const el = screen.getByText("x");
    expect(el.className).toContain("text-fg-secondary");
  });

  it("merges a custom className", () => {
    render(
      <Badge tone="gold" className="ml-2">
        embry0
      </Badge>,
    );
    const el = screen.getByText("embry0");
    expect(el.className).toContain("ml-2");
    expect(el.className).toContain("text-primary");
  });

  it("does not ring on first mount, nor when it says the same again", () => {
    allowMotion();
    const { rerender } = render(<Badge tone="info">Running</Badge>);
    expect(ring()).toBeNull();
    rerender(<Badge tone="info">Running</Badge>);
    expect(ring()).toBeNull();
  });

  it("rings once when its words change, and the ring leaves when it ends", () => {
    allowMotion();
    const { rerender } = render(<Badge tone="info">Running</Badge>);
    rerender(<Badge tone="info">Completed</Badge>);
    const el = ring();
    expect(el).not.toBeNull();
    expect(el).toHaveClass("motion-ring");
    // An outline in the badge's own colour, from the utilities.
    expect(el).toHaveClass("border", "border-current");
    expect(el).toHaveAttribute("aria-hidden");
    expect(screen.getByText("Completed")).toHaveClass("relative");
    // jsdom has no AnimationEvent, so React listens for the prefixed name
    // there; fire both and the test holds wherever it runs.
    fireEvent.animationEnd(el!);
    fireEvent(el!, new Event("webkitAnimationEnd", { bubbles: true }));
    expect(ring()).toBeNull();
  });

  it("rings when only its tone changes", () => {
    allowMotion();
    const { rerender } = render(<Badge tone="info">Checks</Badge>);
    rerender(<Badge tone="success">Checks</Badge>);
    expect(ring()).not.toBeNull();
  });

  it("stays still under reduced motion", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
    const { rerender } = render(<Badge tone="info">Running</Badge>);
    rerender(<Badge tone="success">Completed</Badge>);
    expect(ring()).toBeNull();
  });

  it("never rings in gold", () => {
    allowMotion();
    const { rerender } = render(<Badge tone="info">Waiting</Badge>);
    rerender(<Badge tone="gold">Your turn</Badge>);
    expect(ring()).toBeNull();
  });
});
