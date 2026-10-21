import { describe, expect, it } from "vitest";
import css from "../index.css?raw";

/**
 * Tailwind puts its utilities in a cascade layer, and a rule outside every
 * layer beats any rule inside one. The app's default border colour (Tailwind's
 * own default is currentColor) applies to every element, so outside a layer
 * it hid every border colour utility: border-primary/40 drew the neutral
 * border. jsdom parses layers but leaves layered rules out of its cascade, so
 * these tests read the stylesheet's rules instead of computed styles.
 */

function sheetRules(): CSSRule[] {
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);
  const rules = Array.from(style.sheet?.cssRules ?? []);
  style.remove();
  return rules;
}

/** Style rules outside every layer, through @media and @supports. */
function unlayered(rules: CSSRule[]): CSSStyleRule[] {
  return rules.flatMap((rule) => {
    if (rule instanceof CSSStyleRule) return [rule];
    if (rule instanceof CSSLayerBlockRule) return [];
    if (rule instanceof CSSGroupingRule) return unlayered(Array.from(rule.cssRules));
    return [];
  });
}

function inLayer(rules: CSSRule[], name: string): CSSStyleRule[] {
  return rules.flatMap((rule) =>
    rule instanceof CSSLayerBlockRule && rule.name === name
      ? Array.from(rule.cssRules).filter((r): r is CSSStyleRule => r instanceof CSSStyleRule)
      : [],
  );
}

const everyElement = (rule: CSSStyleRule) => rule.selectorText.split(",").some((s) => s.trim() === "*");

describe("index.css border colour", () => {
  it("gives every element the neutral border colour in the base layer", () => {
    const base = inLayer(sheetRules(), "base").filter(everyElement);
    expect(base.map((r) => r.style.getPropertyValue("border-color"))).toContain("var(--color-border)");
  });

  it("sets no border colour on every element outside a layer, where it would beat the utilities", () => {
    const loose = unlayered(sheetRules())
      .filter(everyElement)
      .filter((r) => /border/.test(r.style.cssText));
    expect(loose.map((r) => r.cssText)).toEqual([]);
  });
});
