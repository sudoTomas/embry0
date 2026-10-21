import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { Edge, Node } from "@xyflow/react";
import css from "../../../index.css?raw";

vi.mock("@/hooks/useAgents", () => ({ useAgents: () => ({ data: [] }) }));
vi.mock("@/hooks/useSkills", () => ({ useSkills: () => ({ data: [], isLoading: false }) }));

import { AgentDetailPopup } from "../AgentDetailPopup";
import { EdgeDetailPopup } from "../EdgeDetailPopup";

/**
 * The editor's detail popups share one entrance, kept in index.css with
 * the rest of the motion. The agent popup used to name keyframes that only
 * the edge popup's own <style> defined, so it rose in only while an edge
 * popup was mounted, which the editor never does at the same time; and
 * neither entrance stood still under reduced motion.
 */

const node = { id: "n1", position: { x: 0, y: 0 }, data: { agentType: "developer" } } as Node;
const edge = { id: "e1", source: "triage", target: "developer", data: {} } as Edge;

function inlineAnimations(container: HTMLElement): string[] {
  return [...container.querySelectorAll<HTMLElement>("*")]
    .map((el) => el.style.animation || el.style.animationName)
    .filter(Boolean);
}

function sheetRules(): CSSRule[] {
  const style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);
  const rules = Array.from(style.sheet?.cssRules ?? []);
  style.remove();
  return rules;
}

describe("pipeline editor popup entrance", () => {
  it("gives the agent popup the shared entrance, not an animation of its own", () => {
    const { container } = render(<AgentDetailPopup node={node} onUpdate={() => {}} onClose={() => {}} />);
    expect(screen.getByRole("dialog")).toHaveClass("pipeline-popup-in");
    expect(inlineAnimations(container)).toEqual([]);
  });

  it("gives the edge popup the shared entrance, with no stylesheet of its own", () => {
    const { container } = render(<EdgeDetailPopup edge={edge} onUpdate={() => {}} onClose={() => {}} />);
    expect(container.querySelectorAll("style")).toHaveLength(0);
    expect(container.querySelector(".pipeline-popup-in")).toHaveTextContent("Flow Edge");
    expect(inlineAnimations(container)).toEqual([]);
  });

  it("keeps the entrance's keyframes in index.css, moving only transform and opacity", () => {
    const keyframes = sheetRules().filter(
      (r): r is CSSKeyframesRule => r instanceof CSSKeyframesRule && r.name === "pipeline-popup-in",
    );
    expect(keyframes).toHaveLength(1);
    const props = Array.from(keyframes[0].cssRules).flatMap((frame) =>
      Array.from((frame as CSSKeyframeRule).style),
    );
    expect(props.length).toBeGreaterThan(0);
    expect(props.filter((p) => p !== "transform" && p !== "opacity")).toEqual([]);
  });

  it("runs the entrance only for a person who has not asked for reduced motion", () => {
    const owners = sheetRules().flatMap((rule) => {
      const inner = rule instanceof CSSMediaRule ? Array.from(rule.cssRules) : [rule];
      return inner
        .filter((r): r is CSSStyleRule => r instanceof CSSStyleRule && r.selectorText === ".pipeline-popup-in")
        .map(() => (rule instanceof CSSMediaRule ? rule.media.mediaText : "all"));
    });
    expect(owners).toEqual(["(prefers-reduced-motion: no-preference)"]);
  });
});
