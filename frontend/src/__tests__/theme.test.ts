import { describe, expect, it } from "vitest";
import css from "../index.css?raw";
import html from "../../index.html?raw";
import { PALETTE, PALETTE_HEX } from "@/lib/agentIdentity";

/**
 * One theme (spec 3.1). index.css holds the only token set; the palette
 * tokens equal the identity module's hex so a sprite tinted by CSS and a
 * badge tinted by class agree; the second token set, the role and stage
 * tokens and the dead keyframes are gone; the chrome that hard-coded hex
 * reads tokens. The pixel face is a token three classes use.
 */

const CHROME = import.meta.glob(
  [
    "/src/components/layout/Sidebar.tsx",
    "/src/components/layout/TopBar.tsx",
    "/src/components/pipeline-editor/PipelineEditor.tsx",
    "/src/components/pipeline-editor/StartEndNode.tsx",
    "/src/components/pipeline-editor/AgentBar.tsx",
    "/src/components/pipeline-editor/FeedbackEdge.tsx",
    "/src/components/pipeline-editor/hooks/useGraphState.ts",
  ],
  { query: "?raw", import: "default", eager: true },
) as Record<string, string>;

const SOURCES = import.meta.glob(
  ["/src/**/*.{ts,tsx,css}", "!/src/**/__tests__/**", "!/src/**/*.test.{ts,tsx}", "!/src/index.css"],
  { query: "?raw", import: "default", eager: true },
) as Record<string, string>;

function theme(): string {
  const start = css.indexOf("@theme {");
  const end = css.indexOf("\n}", start);
  expect(start, "index.css has one @theme block").toBeGreaterThan(-1);
  return css.slice(start, end);
}

describe("theme tokens", () => {
  it("imports Outfit, JetBrains Mono and Pixelify Sans, and declares the pixel face", () => {
    expect(css.split("\n")[0]).toMatch(/^@import url\('https:\/\/fonts\.googleapis\.com\/css2\?family=Outfit.*JetBrains\+Mono.*Pixelify\+Sans.*display=swap'\);$/);
    expect(theme()).toContain("--font-pixel: 'Pixelify Sans', monospace;");
    expect(html).not.toContain("IBM Plex");
    expect(html).not.toMatch(/fonts\.googleapis\.com\/css2/);
    expect(html).toContain('rel="preconnect" href="https://fonts.googleapis.com"');
  });

  it("declares one palette token per identity colour, equal to PALETTE_HEX", () => {
    const block = theme();
    for (const id of PALETTE) {
      expect(block).toContain(`--color-palette-${id}: ${PALETTE_HEX[id]};`);
    }
  });

  it("declares the surface tokens the chrome reads", () => {
    const block = theme();
    for (const token of ["--color-canvas:", "--color-elevated:", "--color-grid:", "--color-feedback:"]) {
      expect(block).toContain(token);
    }
  });

  it("has no second token set, no role or stage tokens and no dead keyframes", () => {
    expect(css).not.toMatch(/--embry0-/);
    expect(css).not.toContain(".embry0-card");
    expect(css).not.toContain(".embry0-spinner");
    expect(css).not.toMatch(/--color-(role|stage)-/);
    for (const name of ["embry0-flow-down", "embry0-pulse-border", "embry0-spin", "agent-bounce", "pulse-glow"]) {
      expect(css, `keyframes ${name} removed`).not.toContain(`@keyframes ${name}`);
    }
    expect(css).not.toMatch(/\.glow-(cyan|amber|green|purple|blue)/);
  });

  it("writes the React Flow overrides with tokens, not hex", () => {
    const start = css.indexOf("/* React Flow");
    const end = css.indexOf("/*", start + 2);
    const block = css.slice(start, end);
    expect(block.length).toBeGreaterThan(100);
    expect(block).not.toMatch(/#[0-9a-fA-F]{3,8}\b/);
  });

  it("uses the pixel face in exactly .hud and .title-pixel here", () => {
    const rules = [...css.matchAll(/\n(\.[a-z-]+)\s*\{[^}]*var\(--font-pixel\)[^}]*\}/g)].map((m) => m[1]);
    expect(rules.sort()).toEqual([".hud", ".title-pixel"]);
  });
});

describe("chrome", () => {
  it("hard-codes no hex colour", () => {
    for (const [file, source] of Object.entries(CHROME)) {
      const hits = [...source.matchAll(/#[0-9a-fA-F]{3,8}\b/g)].map((m) => m[0]);
      expect(hits, file).toEqual([]);
    }
  });

  it("no source uses the legacy card, the role tokens or the pixel utility directly", () => {
    const hits: string[] = [];
    for (const [file, source] of Object.entries(SOURCES)) {
      if (/\bembry0-card\b/.test(source)) hits.push(`${file}: embry0-card`);
      if (/\b(text|bg|border)-(role|stage)-/.test(source)) hits.push(`${file}: role/stage token`);
      if (/\bfont-pixel\b/.test(source) && !file.endsWith(".css")) hits.push(`${file}: font-pixel`);
      if (/animate-agent-bounce|animate-pulse-glow/.test(source)) hits.push(`${file}: dead animation class`);
    }
    expect(hits).toEqual([]);
  });

  it("gives every page heading the pixel face", () => {
    const pages = Object.entries(SOURCES).filter(([file]) => file.startsWith("/src/pages/") && file.endsWith(".tsx"));
    expect(pages.length).toBeGreaterThan(20);
    const bare: string[] = [];
    for (const [file, source] of pages) {
      for (const m of source.matchAll(/<h1\b[^>]*>/g)) {
        if (!/\btitle-pixel\b/.test(m[0])) bare.push(`${file}: ${m[0]}`);
      }
    }
    expect(bare).toEqual([]);
  });
});
