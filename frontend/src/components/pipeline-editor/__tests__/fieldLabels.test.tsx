import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Edge, Node } from "@xyflow/react";

vi.mock("@/hooks/useAgents", () => ({ useAgents: () => ({ data: [] }) }));

import { AgentDetailPopup } from "../AgentDetailPopup";
import { EdgeDetailPopup } from "../EdgeDetailPopup";

/**
 * The popups' field labels were <label> elements tied to nothing, so a
 * screen reader announced each field by its type alone and a click on a
 * label focused nothing.
 */

function unnamedFields(dialog: HTMLElement): string[] {
  return [...dialog.querySelectorAll<HTMLInputElement>("input, select, textarea")]
    .filter((field) => !field.labels?.length && !field.getAttribute("aria-label"))
    .map((field) => field.outerHTML);
}

describe("the pipeline popups' field labels", () => {
  it("name each field of a feedback loop", () => {
    const edge = {
      id: "fb",
      source: "reviewer",
      target: "developer",
      data: { edgeType: "feedback", loopConfig: { max_loops: 3, max_loop_budget_usd: 2, feedback_mode: "diff" } },
    } as Edge;
    render(<EdgeDetailPopup edge={edge} onUpdate={vi.fn()} onClose={vi.fn()} />);

    expect(screen.getByLabelText("Max Loops")).toHaveValue(3);
    expect(screen.getByLabelText("Loop Budget (USD)")).toHaveValue(2);
    expect(screen.getByLabelText("Feedback Mode")).toHaveValue("diff");
    expect(unnamedFields(screen.getByRole("dialog"))).toEqual([]);
  });

  it("name each field of an agent's configuration", async () => {
    const user = userEvent.setup();
    const node = {
      id: "n1",
      position: { x: 0, y: 0 },
      data: {
        agentType: "developer",
        maxBudgetUsd: 4,
        maxTurns: 30,
        effort: "high",
        skills: [{ name: "lint", mode: "guided" }],
        promptPrepend: "Before",
        promptAppend: "After",
        sandbox: { image: "sandbox:test" },
      },
    } as Node;
    render(<AgentDetailPopup node={node} onUpdate={vi.fn()} onClose={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Edit Configuration" }));
    await user.click(screen.getByRole("button", { name: "Advanced" }));

    expect(screen.getByLabelText("Model")).toHaveValue("");
    expect(screen.getByLabelText("Budget (USD)")).toHaveValue(4);
    expect(screen.getByLabelText("Max Turns")).toHaveValue(30);
    expect(screen.getByLabelText("Effort")).toHaveValue("high");
    expect(screen.getByLabelText("Tools")).toHaveAttribute("placeholder", "Read, Write, Bash...");
    expect(screen.getByLabelText("Skills")).toHaveAttribute("placeholder", "Add skill...");
    expect(screen.getByLabelText("lint mode")).toHaveValue("guided");
    expect(screen.getByLabelText("Prepend")).toHaveValue("Before");
    expect(screen.getByLabelText("Append")).toHaveValue("After");
    expect(screen.getByLabelText("Sandbox Image")).toHaveValue("sandbox:test");
    expect(unnamedFields(screen.getByRole("dialog"))).toEqual([]);
  });

  it("focuses a field when its label is clicked", async () => {
    const user = userEvent.setup();
    const edge = { id: "fb", source: "reviewer", target: "developer", data: { edgeType: "feedback" } } as Edge;
    render(<EdgeDetailPopup edge={edge} onUpdate={vi.fn()} onClose={vi.fn()} />);

    await user.click(screen.getByText("Loop Budget (USD)"));

    expect(document.activeElement).toBe(screen.getByLabelText("Loop Budget (USD)"));
  });
});
