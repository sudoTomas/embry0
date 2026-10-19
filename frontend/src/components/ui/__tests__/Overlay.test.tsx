import { createRef } from "react";
import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import ts from "typescript";
import { Overlay } from "../Overlay";

const SOURCES = import.meta.glob(
  [
    "/src/**/*.{ts,tsx}",
    "!/src/**/__tests__/**",
    "!/src/**/*.test.{ts,tsx}",
    "!/src/test-setup.ts",
    "!/src/components/ui/Overlay.tsx",
  ],
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

const coversWindow = (classes: string) => {
  const tokens = classes.split(/\s+/);
  return tokens.includes("fixed") && tokens.includes("inset-0");
};

describe("Overlay", () => {
  // Tailwind's space-y puts a bottom margin on every child but the last,
  // which cut 24 px off a fixed overlay rendered inline among them.
  it("renders over the window as a child of body, not of the component's parent", () => {
    const ref = createRef<HTMLDivElement>();
    const { container } = render(
      <div className="space-y-6">
        <p>Before</p>
        <Overlay ref={ref} data-testid="layer" className="bg-black/60" />
        <p>After</p>
      </div>,
    );
    const layer = screen.getByTestId("layer");
    expect(layer.parentElement).toBe(document.body);
    expect(container).not.toContainElement(layer);
    expect(ref.current).toBe(layer);
    expect(layer).toHaveClass("fixed", "inset-0", "z-50", "bg-black/60");
  });

  it("is the only box drawn over the whole window", () => {
    const inline = Object.entries(SOURCES)
      .filter(([file, source]) => strings(file, source).some(coversWindow))
      .map(([file]) => file);
    expect(inline, "render a full-window layer with <Overlay>, not an inline fixed inset-0 box").toEqual([]);
  });
});
