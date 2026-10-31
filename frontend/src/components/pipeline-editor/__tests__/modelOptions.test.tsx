import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { describe, expect, it, vi } from "vitest";
import type { Node } from "@xyflow/react";

/**
 * The step editor offers the models the agent definitions name. A brief
 * writer's voice names none (its model is background_models'), so its empty
 * model must not become a second, blank "Default" option.
 */
vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => ({
    data: [
      { type: "developer", model: "claude-opus-5", skills: [], display_name: "Developer", avatar: "smith", color: "amber" },
      { type: "review", model: "claude-opus-5", skills: [], display_name: "Reviewer", avatar: "warden", color: "rose" },
      { type: "triage", model: "claude-sonnet-5", skills: [], display_name: "Triage", avatar: "sage", color: "sky" },
      { type: "proposal-brief", model: "", runs_in_jobs: false, skills: [], display_name: "Briefs", avatar: "scribe", color: "slate" },
      { type: "job-brief", model: "", runs_in_jobs: false, skills: [], display_name: "Job briefs", avatar: "scribe", color: "slate" },
    ],
  }),
}));
vi.mock("@/hooks/useSkills", () => ({ useSkills: () => ({ data: [], isLoading: false }) }));
import { AgentDetailPopup } from "../AgentDetailPopup";

describe("the step editor's model options", () => {
  it("lists each model an agent names once, and no blank option besides the default", async () => {
    const user = userEvent.setup();
    const node = { id: "d", type: "agentNode", position: { x: 0, y: 0 }, data: { agentType: "developer", label: "developer" } } as Node;
    render(<MemoryRouter><AgentDetailPopup node={node} onUpdate={vi.fn()} onClose={vi.fn()} /></MemoryRouter>);
    await user.click(screen.getByRole("button", { name: "Edit Configuration" }));

    const options = within(screen.getByLabelText("Model")).getAllByRole("option") as HTMLOptionElement[];
    expect(options.map((o) => [o.value, o.textContent])).toEqual([
      ["", "Default (from registry)"],
      ["claude-opus-5", "claude-opus-5"],
      ["claude-sonnet-5", "claude-sonnet-5"],
    ]);
  });
});
