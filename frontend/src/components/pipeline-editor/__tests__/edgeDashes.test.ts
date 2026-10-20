import { describe, expect, it } from "vitest";
import css from "../../../index.css?raw";

/**
 * React Flow marches an animated edge's dashes every 0.5 s and ignores
 * reduced motion. The dashboard's loops run no quicker than 2 s and stand
 * still under reduced motion, so index.css slows the dashes and stills them.
 */

function sheetRules(): CSSRule[] {
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);
  const rules = Array.from(style.sheet?.cssRules ?? []);
  style.remove();
  return rules;
}

const SELECTOR = ".react-flow .react-flow__edge.animated path";

describe("a feedback edge's marching dashes", () => {
  it("march no quicker than the dashboard's 2 s loop floor", () => {
    const rule = sheetRules().find(
      (r): r is CSSStyleRule => r instanceof CSSStyleRule && r.selectorText === SELECTOR,
    );
    expect(rule?.style.animationDuration).toBe("2s");
  });

  it("stand still for anyone who asked for reduced motion", () => {
    const reduced = sheetRules().filter(
      (r): r is CSSMediaRule => r instanceof CSSMediaRule && /reduce\b/.test(r.conditionText) && !/no-preference/.test(r.conditionText),
    );
    const stills = reduced.flatMap((m) => Array.from(m.cssRules)).find(
      (r): r is CSSStyleRule => r instanceof CSSStyleRule && r.selectorText === SELECTOR,
    );
    expect(stills?.style.animation || stills?.style.animationName).toMatch(/none/);
  });
});
