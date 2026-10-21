import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, renderHook, screen, within } from "@testing-library/react";

vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => ({
    data: [
      { type: "developer", display_name: "Developer", avatar: "smith", color: "amber", skills: [] },
      { type: "review", display_name: "Reviewer", avatar: "warden", color: "violet", skills: [] },
    ],
  }),
}));
import { PartyStrip } from "../PartyStrip";
import { useAgentStates } from "@/hooks/useAgentStates";
import { loadFixture } from "@/lib/__tests__/fixtures";

function agentsOf(name: "issue-to-pr" | "legacy-rows", status?: string) {
  const events = loadFixture(name);
  const graph = events.find((e) => e.type === "pipeline_graph");
  const { result } = renderHook(() => useAgentStates(events, status, graph && graph.type === "pipeline_graph" ? graph.graph : null));
  return result.current.agents;
}

describe("PartyStrip", () => {
  it("shows one sprite per run, in run order, in the run's state", () => {
    const agents = agentsOf("issue-to-pr", "completed");
    render(<PartyStrip agents={agents} selectedRunId={null} onSelect={() => {}} />);
    const strip = screen.getByTestId("party-strip");
    const buttons = within(strip).getAllByRole("button");
    expect(buttons.map((b) => b.getAttribute("data-run-id")), "strip order must be run order").toEqual(agents.map((a) => a.runId));
    expect(buttons.map((b) => b.getAttribute("data-state"))).toEqual(agents.map((a) => (a.status === "failed" ? "failed" : "done")));
    expect(within(strip).getAllByText("Developer").length, "display name under the sprite").toBeGreaterThan(0);
    expect(strip.querySelectorAll("svg.sprite").length, "exactly one sprite per run").toBe(agents.length);
  });

  it("marks the selected run and clears it on a second click", () => {
    const agents = agentsOf("issue-to-pr", "completed");
    const onSelect = vi.fn();
    render(<PartyStrip agents={agents} selectedRunId={agents[1].runId} onSelect={onSelect} />);
    const buttons = screen.getAllByRole("button");
    expect(buttons[1].getAttribute("aria-pressed")).toBe("true");
    expect(buttons[0].getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(buttons[1]);
    expect(onSelect, "clicking the selected run clears the selection").toHaveBeenCalledWith(null);
    fireEvent.click(buttons[0]);
    expect(onSelect).toHaveBeenCalledWith(agents[0].runId);
  });

  it("shows pending graph nodes in the pending state while the job runs", () => {
    const agents = agentsOf("issue-to-pr", "running");
    render(<PartyStrip agents={agents} selectedRunId={null} onSelect={() => {}} />);
    const pending = screen.getAllByRole("button").filter((b) => b.getAttribute("data-state") === "pending");
    expect(pending.length, "a running job lists the nodes that have not started").toBe(agents.filter((a) => a.status === "pending").length);
  });

  it("renders legacy runs with no graph", () => {
    const agents = agentsOf("legacy-rows", "completed");
    render(<PartyStrip agents={agents} selectedRunId={null} onSelect={() => {}} />);
    expect(screen.getAllByRole("button").length).toBe(agents.length);
  });
});
