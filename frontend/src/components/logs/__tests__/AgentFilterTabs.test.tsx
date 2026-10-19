import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => ({
    data: [
      { type: "developer", display_name: "Developer", avatar: "smith", color: "amber" },
      { type: "review", display_name: "Reviewer", avatar: "warden", color: "rose" },
      { type: "pr-reviewer", display_name: "PR Reviewer", avatar: "arbiter", color: "cyan" },
    ],
  }),
}));
import { AgentFilterTabs } from "../AgentFilterTabs";

const states = {
  developer: { node_id: "developer", agent_type: "developer", state: "completed" as const, iteration: 1, cost_usd: 0, turns: 0, duration_seconds: 0 },
  reviewer: { node_id: "reviewer", agent_type: "reviewer", state: "running" as const, iteration: 1, cost_usd: 0, turns: 0, duration_seconds: 0 },
  "pr-reviewer": { node_id: "pr-reviewer", agent_type: "pr-reviewer", state: "pending" as const, iteration: 1, cost_usd: 0, turns: 0, duration_seconds: 0 },
};

describe("AgentFilterTabs", () => {
  it("colours each tab from the agent's identity, resolving the reviewer spelling", () => {
    render(<AgentFilterTabs nodeStates={states} selectedNodeId={null} onSelect={() => {}} />);
    expect(screen.getByText("Developer").className).toContain("text-amber-400");
    expect(screen.getByText("Reviewer").className).toContain("text-rose-400");
  });

  it("labels a tab with the display name, not a re-cased slug", () => {
    render(<AgentFilterTabs nodeStates={states} selectedNodeId={null} onSelect={() => {}} />);
    // Re-casing the slug gives "Pr Reviewer"; the row says "PR Reviewer".
    expect(screen.getByText("PR Reviewer")).toBeInTheDocument();
    expect(screen.queryByText("Pr Reviewer")).toBeNull();
  });
});
