import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { Edge, Node } from "@xyflow/react";
import css from "../../../index.css?raw";

vi.mock("@/hooks/useAgents", () => ({ useAgents: () => ({ data: [] }) }));
vi.mock("@/hooks/useSkills", () => ({ useSkills: () => ({ data: [], isLoading: false }) }));

import { AgentDetailPopup } from "../AgentDetailPopup";
import { EdgeDetailPopup } from "../EdgeDetailPopup";

/**
 * The editor's detail popups are Dialogs (spec 3.9): they rise in with the
 * dialog entrance that index.css owns, and the editor's own entrance of old
 * is gone with the hand-rolled shells that used it.
 */

const node = { id: "n1", position: { x: 0, y: 0 }, data: { agentType: "developer" } } as Node;
const edge = { id: "e1", source: "triage", target: "developer", data: {} } as Edge;

function inlineAnimations(): string[] {
  return [...document.body.querySelectorAll<HTMLElement>("*")]
    .map((el) => el.style.animation || el.style.animationName)
    .filter(Boolean);
}

describe("pipeline editor popup entrance", () => {
  it("gives the agent popup the dialog entrance, over the window", () => {
    render(<AgentDetailPopup node={node} onUpdate={() => {}} onClose={() => {}} />);
    const dialog = screen.getByRole("dialog", { name: "Developer" });
    expect(dialog).toHaveClass("dialog-in");
    expect(dialog.parentElement?.parentElement, "a Dialog, so a child of body, not of the canvas").toBe(document.body);
    expect(inlineAnimations()).toEqual([]);
  });

  it("gives the edge popup the dialog entrance, with no stylesheet of its own", () => {
    const { container } = render(<EdgeDetailPopup edge={edge} onUpdate={() => {}} onClose={() => {}} />);
    expect(container.querySelectorAll("style")).toHaveLength(0);
    const dialog = screen.getByRole("dialog", { name: "Flow edge from triage to developer" });
    expect(dialog).toHaveClass("dialog-in");
    expect(inlineAnimations()).toEqual([]);
  });

  it("widens each popup with a max-w-* class, since the panel is w-full under max-w-lg", () => {
    render(<AgentDetailPopup node={node} onUpdate={() => {}} onClose={() => {}} />);
    const agent = screen.getByRole("dialog", { name: "Developer" });
    expect(agent, "a w-[560px] loses to Dialog's max-w-lg; max-w-[560px] replaces it").toHaveClass("max-w-[560px]");
    expect(agent).not.toHaveClass("max-w-lg");
    render(<EdgeDetailPopup edge={edge} onUpdate={() => {}} onClose={() => {}} />);
    const edgeDialog = screen.getByRole("dialog", { name: "Flow edge from triage to developer" });
    expect(edgeDialog).toHaveClass("max-w-[400px]");
    expect(edgeDialog).not.toHaveClass("max-w-lg");
  });

  it("left no entrance of its own behind in index.css", () => {
    expect(css, "the pipeline-popup-in keyframes went with the hand-rolled shells").not.toContain("pipeline-popup-in");
  });
});
