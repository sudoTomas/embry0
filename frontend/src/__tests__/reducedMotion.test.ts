import { describe, expect, it } from "vitest";
import ts from "typescript";
import indexCss from "../index.css?raw";

/**
 * Nothing moves for a person who asked for reduced motion: they get the
 * final state at once (index.css, "Motion"). Two ways an animation slips
 * past that are checked here.
 *
 * - Tailwind's loops (animate-pulse, animate-bounce, animate-ping) run
 *   whatever the setting, so the dashboard writes them with motion-safe:.
 *   animate-spin is not checked: a spinner's turning is what it says.
 * - A class of index.css that runs an animation and that the app uses sits
 *   in a `prefers-reduced-motion: no-preference` block, or a `reduce` block
 *   sets its animation back to none.
 *
 * A failure names the file and the class.
 */

const SOURCES = import.meta.glob(
  ["/src/**/*.{ts,tsx}", "!/src/**/__tests__/**", "!/src/**/*.test.{ts,tsx}", "!/src/test-setup.ts"],
  { query: "?raw", import: "default", eager: true },
) as Record<string, string>;

/** String literals and template text: where class names live, not comments. */
function strings(fileName: string, source: string): string[] {
  const kind = fileName.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const file = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, kind);
  const out: string[] = [];
  const visit = (node: ts.Node): void => {
    if (
      ts.isStringLiteral(node) ||
      ts.isNoSubstitutionTemplateLiteral(node) ||
      ts.isTemplateHead(node) ||
      ts.isTemplateMiddle(node) ||
      ts.isTemplateTail(node)
    ) {
      out.push(node.text);
    }
    ts.forEachChild(node, visit);
  };
  visit(file);
  return out;
}

/** Tailwind loop utilities written without motion-safe:, with their variants. */
function unsafeLoops(text: string): string[] {
  const LOOP = /(?<![\w:-])((?:[\w-]+:)*)animate-(?:pulse|bounce|ping)(?![\w-])/g;
  return [...text.matchAll(LOOP)].filter((m) => !m[1].split(":").includes("motion-safe")).map((m) => m[0]);
}

interface AnimatedRule {
  selector: string;
  /** The at-rules the rule sits in, outermost first. */
  within: string[];
  animation: string;
}

/**
 * Every rule that sets `animation` or `animation-name`, with the at-rules
 * around it. Enough of a CSS reader for index.css: comments out, then
 * braces and semicolons; keyframe bodies are skipped.
 */
function animatedRules(css: string): AnimatedRule[] {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const out: AnimatedRule[] = [];
  const stack: string[] = [];
  let buf = "";
  const declaration = () => {
    const colon = buf.indexOf(":");
    const prop = buf.slice(0, colon).trim();
    if (colon > 0 && stack.length > 0 && (prop === "animation" || prop === "animation-name")) {
      const within = stack.slice(0, -1);
      if (!within.some((at) => at.startsWith("@keyframes"))) {
        out.push({ selector: stack[stack.length - 1], within, animation: buf.slice(colon + 1).trim() });
      }
    }
    buf = "";
  };
  for (const ch of text) {
    if (ch === "{") {
      stack.push(buf.trim().replace(/\s+/g, " "));
      buf = "";
    } else if (ch === "}") {
      declaration();
      stack.pop();
    } else if (ch === ";") {
      declaration();
    } else {
      buf += ch;
    }
  }
  return out;
}

const guarded = (rule: AnimatedRule) =>
  rule.within.some((at) => /prefers-reduced-motion:\s*no-preference/.test(at));

/** Selectors a `reduce` block sets back to `animation: none`. */
function stilled(rules: AnimatedRule[]): Set<string> {
  return new Set(
    rules
      .filter((r) => r.animation === "none" && r.within.some((at) => /prefers-reduced-motion:\s*reduce/.test(at)))
      .map((r) => r.selector),
  );
}

/** Classes of index.css that animate under reduced motion. */
function unguardedClasses(css: string): string[] {
  const rules = animatedRules(css);
  const still = stilled(rules);
  return rules
    .filter((r) => r.animation !== "none" && !guarded(r) && !still.has(r.selector))
    .flatMap((r) => [...r.selector.matchAll(/\.([\w-]+)/g)].map((m) => m[1]));
}

/** Every class token the app writes in a string, variants stripped. */
const USED_CLASSES = new Set(
  Object.entries(SOURCES).flatMap(([fileName, source]) =>
    strings(fileName, source).flatMap((text) => text.split(/\s+/).map((token) => token.split(":").at(-1) ?? "")),
  ),
);

// Samples are put together at run time: Tailwind scans this file too, and
// written out whole they would ship in the stylesheet as classes.
const loop = (name: string) => `animate-${name}`;

describe("reduced motion", () => {
  it("tells a Tailwind loop written with motion-safe: from one without", () => {
    expect(unsafeLoops(`h-3 ${loop("pulse")} rounded`)).toEqual([loop("pulse")]);
    expect(unsafeLoops(`md:${loop("bounce")} ${loop("ping")}`)).toEqual([`md:${loop("bounce")}`, loop("ping")]);
    expect(unsafeLoops(`motion-safe:${loop("pulse")} md:motion-safe:${loop("bounce")}`)).toEqual([]);
    expect(unsafeLoops(`${loop("pulse")}-glow ${loop("spin")}`)).toEqual([]);
  });

  it("tells a guarded animation from one that runs under reduced motion", () => {
    const css = `
      /* .commented { animation: x 1s; } */
      @keyframes x { from { opacity: 0; } }
      .loose { animation: x 1s infinite; }
      .spun { animation-name: x; }
      @media (prefers-reduced-motion: no-preference) {
        .safe { animation: x 1s; }
        @supports (display: grid) { .deep { animation: x 1s; } }
      }
      .marked { animation: x 1s; }
      @media (prefers-reduced-motion: reduce) { .marked { animation: none; } }
      .off { animation: none; }
    `;
    expect(unguardedClasses(css)).toEqual(["loose", "spun"]);
  });

  it("writes Tailwind's loops with motion-safe:, so they hold still under reduced motion", () => {
    const offenders = Object.entries(SOURCES).flatMap(([fileName, source]) =>
      strings(fileName, source).flatMap((text) => unsafeLoops(text).map((cls) => `${fileName}: ${cls}`)),
    );
    expect(offenders).toEqual([]);
  });

  it("guards every animated index.css class the app uses", () => {
    expect(unguardedClasses(indexCss).filter((cls) => USED_CLASSES.has(cls))).toEqual([]);
  });

  it("reads the animated rules of index.css", () => {
    // A reader that found nothing would pass the test above on any file.
    const classes = animatedRules(indexCss).flatMap((r) => [...r.selector.matchAll(/\.([\w-]+)/g)].map((m) => m[1]));
    expect(classes).toEqual(expect.arrayContaining(["motion-enter", "motion-breathe", "embry0-mark"]));
  });
});
