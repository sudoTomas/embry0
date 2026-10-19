import { describe, expect, it } from "vitest";
import ts from "typescript";

/**
 * Text the dashboard shows names no tracker ticket (#528).
 *
 * A ticket key such as `ABC-123` in a comment is provenance and stays. In
 * rendered text, a string literal or a template it points at one
 * deployment's tracker, which no other operator can open. Comments are
 * trivia to the TypeScript parser, so only text that can reach the page
 * is checked. The backend's surfaces are guarded by
 * tests/unit/test_user_facing_text.py.
 *
 * A failure names the file and the text. Move the ticket into a comment.
 */

const SOURCES = import.meta.glob(
  ["/src/**/*.{ts,tsx}", "!/src/**/__tests__/**", "!/src/**/*.test.{ts,tsx}", "!/src/test-setup.ts"],
  { query: "?raw", import: "default", eager: true },
) as Record<string, string>;

const TICKET_REF = /\b([A-Z]{2,}[A-Z0-9]*)-\d+\b/g;
// Words of that shape that are not tickets. ENG is the example key the
// docs and types use; EMBRY0 is the product's own name (the lore page's
// patent number).
const NOT_TICKETS = new Set(["AES", "CVE", "EMBRY0", "ENG", "ISO", "PEP", "RFC", "SHA", "UTF"]);

function ticketRefs(text: string): string[] {
  return [...text.matchAll(TICKET_REF)].filter((m) => !NOT_TICKETS.has(m[1])).map((m) => m[0]);
}

/** JSX text, string literals and template text: everything but comments. */
function visibleText(fileName: string, source: string): string[] {
  const kind = fileName.endsWith(".tsx") ? ts.ScriptKind.TSX : ts.ScriptKind.TS;
  const file = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, kind);
  const out: string[] = [];
  const visit = (node: ts.Node): void => {
    if (
      ts.isJsxText(node) ||
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

describe("user-facing text", () => {
  it("tells a ticket from a lookalike", () => {
    expect(ticketRefs("shipped in ABC-12 and XY-3")).toEqual(["ABC-12", "XY-3"]);
    expect(ticketRefs("UTF-8, SHA-256, ENG-123, A-1, Z0-9, abc-12")).toEqual([]);
  });

  it("reads rendered text, strings and templates, but not comments", () => {
    const source = [
      "// ABC-1 in a line comment",
      "/* ABC-2 in a block comment */",
      'const a = "ABC-3";',
      "const b = `x ${a} ABC-4`;",
      "export const C = () => <p>{/* ABC-5 */}ABC-6</p>;",
    ].join("\n");
    expect(visibleText("probe.tsx", source).flatMap(ticketRefs)).toEqual(["ABC-3", "ABC-4", "ABC-6"]);
  });

  it("names no ticket outside comments in any source file", () => {
    const files = Object.entries(SOURCES);
    expect(files.length, "almost no sources found; the scan would prove nothing").toBeGreaterThan(100);
    const hits = files.flatMap(([file, source]) =>
      visibleText(file, source)
        .flatMap(ticketRefs)
        .map((ref) => `${file}: ${ref}`),
    );
    expect(hits).toEqual([]);
  });
});

describe("pixel face (spec 3.1)", () => {
  it("is reachable only through .hud, .title-pixel and .sprite-nameplate", () => {
    const styles = import.meta.glob(["/src/**/*.css"], { query: "?raw", import: "default", eager: true }) as Record<string, string>;
    const owners = new Set<string>();
    for (const [file, source] of Object.entries(styles)) {
      for (const m of source.matchAll(/\n(\.[a-z-]+)\s*\{[^}]*var\(--font-pixel\)[^}]*\}/g)) owners.add(m[1]);
      const loose = source.replace(/\n\.[a-z-]+\s*\{[^}]*\}/g, "");
      expect(loose.includes("var(--font-pixel)"), `${file}: --font-pixel outside a class rule`).toBe(false);
    }
    const allowed = [".hud", ".title-pixel", ".sprite-nameplate"];
    expect([...owners].filter((o) => !allowed.includes(o))).toEqual([]);
    expect(owners.has(".hud") && owners.has(".title-pixel")).toBe(true);
  });
});
