import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import css from "../roster.css?raw";
import { Sprite } from "../Sprite";

describe("Sprite", () => {
  it("renders a crisp 16x16 image tinted by the palette token, named for assistive tech", () => {
    const { container } = render(<Sprite avatar="smith" color="amber" state="idle" size={32} label="Developer, idle" />);
    const svg = container.querySelector("svg")!;
    expect(svg).toHaveAttribute("role", "img");
    expect(svg).toHaveAttribute("aria-label", "Developer, idle");
    expect(svg).toHaveAttribute("viewBox", "0 0 16 16");
    expect(svg).toHaveAttribute("width", "32");
    expect(svg).toHaveAttribute("shape-rendering", "crispEdges");
    expect(svg).toHaveAttribute("data-avatar", "smith");
    expect(svg).toHaveAttribute("data-color", "amber");
    expect(svg).toHaveAttribute("data-state", "idle");
    expect(svg.style.getPropertyValue("--sprite-fill")).toBe("var(--color-palette-amber)");
    const fills = [...svg.querySelectorAll("path")].map((p) => p.getAttribute("fill"));
    expect(fills).toContain("var(--color-background)");
    expect(fills).toContain("var(--sprite-fill)");
    expect(fills).toContain("var(--color-foreground)");
  });

  it("stacks both frames of a two-frame state and one frame otherwise", () => {
    const { container, rerender } = render(<Sprite avatar="seer" color="violet" state="thinking" />);
    expect(container.querySelector("svg")).toHaveAttribute("data-frames", "2");
    expect(container.querySelectorAll("g.sprite-frame")).toHaveLength(2);
    expect(container.querySelector('g[data-frame="1"]')).not.toBeNull();
    rerender(<Sprite avatar="seer" color="violet" state="done" />);
    expect(container.querySelector("svg")).toHaveAttribute("data-frames", "1");
    expect(container.querySelectorAll("g.sprite-frame")).toHaveLength(1);
  });

  it("falls back to golem and slate for ids it does not know", () => {
    const { container } = render(<Sprite avatar="dragon" color="mauve" state="idle" />);
    const svg = container.querySelector("svg")!;
    expect(svg).toHaveAttribute("data-avatar", "golem");
    expect(svg).toHaveAttribute("data-color", "slate");
    expect(svg.getAttribute("aria-label")).toBe("golem, idle");
  });

  it("draws the pending outline as a dim tint, not the background colour it would vanish into", () => {
    const { container, rerender } = render(<Sprite avatar="smith" color="amber" state="pending" />);
    let fills = [...container.querySelectorAll("path")].map((p) => p.getAttribute("fill"));
    expect(fills).toEqual(["color-mix(in srgb, var(--sprite-fill) 45%, transparent)"]);
    rerender(<Sprite avatar="smith" color="amber" state="idle" />);
    fills = [...container.querySelectorAll("path")].map((p) => p.getAttribute("fill"));
    expect(fills).toContain("var(--color-background)");
    expect(fills).not.toContain("color-mix(in srgb, var(--sprite-fill) 45%, transparent)");
  });

  it("defaults to 32 px", () => {
    const { container } = render(<Sprite avatar="bard" color="gold" state="pending" />);
    expect(container.querySelector("svg")).toHaveAttribute("height", "32");
  });

  it("animates only under prefers-reduced-motion: no-preference, with frame 1 hidden otherwise", () => {
    const gated = css.slice(css.indexOf("@media (prefers-reduced-motion: no-preference)"));
    const open = css.slice(0, css.indexOf("@media (prefers-reduced-motion: no-preference)"));
    expect(open).not.toContain("animation:");
    expect(open).toMatch(/\.sprite-frame\[data-frame="1"\]\s*\{\s*opacity:\s*0;?\s*\}/);
    expect(gated).toContain("@keyframes roster-step");
    expect(gated).toMatch(/animation:\s*roster-step\s+1\.2s\s+steps\(1, end\)\s+infinite/);
    expect(gated).toMatch(/animation-delay:\s*-0\.6s/);
  });

  it("gives the nameplate the pixel face", () => {
    expect(css).toMatch(/\n\.sprite-nameplate\s*\{[^}]*font-family:\s*var\(--font-pixel\)/);
  });
});
