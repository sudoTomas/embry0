import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PRESET_IDS } from "../appearance";
import css from "../roster.css?raw";
import { Sprite } from "../Sprite";

describe("Sprite", () => {
  it("renders a crisp 16x16 image tinted by the palette token, named for assistive tech", () => {
    const { container } = render(<Sprite avatar="smith" color="amber" state="idle" size={32} label="Developer" />);
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
    expect(fills).toContain("color-mix(in srgb, var(--sprite-fill) 30%, black)");
    expect(fills).toContain("var(--sprite-fill)");
    expect(fills).toContain("var(--color-background)");
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

  it("draws a still sprite as frame 0 alone, so the stepping animation has nothing to step", () => {
    const { container } = render(<Sprite avatar="seer" color="violet" state="thinking" still />);
    const svg = container.querySelector("svg")!;
    expect(svg, "the CSS animates only data-frames=2").toHaveAttribute("data-frames", "1");
    expect(svg).toHaveAttribute("data-state", "thinking");
    const frames = container.querySelectorAll("g.sprite-frame");
    expect(frames).toHaveLength(1);
    expect(frames[0]).toHaveAttribute("data-frame", "0");
  });

  it.each([
    ["idle", "Developer, idle"],
    ["thinking", "Developer, thinking"],
    ["tool", "Developer, using a tool"],
    ["done", "Developer, done"],
    ["failed", "Developer, failed"],
    ["pending", "Developer, waiting"],
  ] as const)("names the %s state in words", (state, name) => {
    const { container } = render(<Sprite avatar="smith" color="amber" state={state} label="Developer" />);
    expect(container.querySelector("svg")).toHaveAttribute("aria-label", name);
  });

  it("is hidden from assistive tech when decorative, leaving its control's name to stand alone", () => {
    const { container } = render(<Sprite avatar="rogue" color="cyan" state="idle" decorative />);
    const svg = container.querySelector("svg")!;
    expect(svg).toHaveAttribute("aria-hidden", "true");
    expect(svg).not.toHaveAttribute("aria-label");
    expect(svg).not.toHaveAttribute("role");
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

  describe("with an adventurer preset", () => {
    it("draws the composed 32x32 frames with their fixed fills and the cape in the party colour", () => {
      const { container } = render(<Sprite avatar="rune-warrior" color="emerald" state="idle" size={48} label="Vanguard" />);
      const svg = container.querySelector("svg")!;
      expect(svg).toHaveAttribute("viewBox", "0 0 32 32");
      expect(svg).toHaveAttribute("width", "48");
      expect(svg).toHaveAttribute("aria-label", "Vanguard, idle");
      expect(svg).toHaveAttribute("data-avatar", "rune-warrior");
      expect(svg).toHaveAttribute("data-color", "emerald");
      expect(svg).toHaveAttribute("data-frames", "2");
      expect(svg.style.getPropertyValue("--sprite-fill")).toBe("var(--color-palette-emerald)");
      const fills = [...svg.querySelectorAll('g[data-frame="0"] path')].map((p) => p.getAttribute("fill")!);
      expect(fills.filter((f) => /^#[0-9a-f]{6}$/.test(f)).length, "fixed hex fills").toBeGreaterThan(8);
      expect(fills, "a rune warrior wears a cape").toContain("var(--sprite-fill)");
      expect(fills).not.toContain("color-mix(in srgb, var(--sprite-fill) 30%, black)");
    });

    it("keeps the legacy avatars at 16x16", () => {
      const { container } = render(<Sprite avatar="smith" color="amber" state="idle" />);
      expect(container.querySelector("svg")).toHaveAttribute("viewBox", "0 0 16 16");
    });

    it("renders every preset in every state, pending as the one dim-tint path", () => {
      for (const id of PRESET_IDS) {
        const { container, unmount } = render(<Sprite avatar={id} color="gold" state="pending" />);
        const fills = [...container.querySelectorAll("path")].map((p) => p.getAttribute("fill"));
        expect(fills, id).toEqual(["color-mix(in srgb, var(--sprite-fill) 45%, transparent)"]);
        unmount();
      }
      for (const state of ["idle", "thinking", "tool", "done", "failed"] as const) {
        const { container, unmount } = render(<Sprite avatar="archmage" color="violet" state={state} />);
        expect(container.querySelectorAll("path").length, state).toBeGreaterThan(5);
        unmount();
      }
    });

    it("draws a still preset as frame 0 alone", () => {
      const { container } = render(<Sprite avatar="scout" color="cyan" state="tool" still />);
      expect(container.querySelector("svg")).toHaveAttribute("data-frames", "1");
      expect(container.querySelectorAll("g.sprite-frame")).toHaveLength(1);
    });
  });
});
