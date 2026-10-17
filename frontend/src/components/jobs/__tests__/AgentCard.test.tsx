import { describe, expect, it } from "vitest";
import { render, renderHook, screen } from "@testing-library/react";
import { AgentCard } from "../AgentCard";
import { useAgentStates, type AgentState } from "@/hooks/useAgentStates";
import { loadFixture } from "@/lib/__tests__/fixtures";

function fixtureAgents(): AgentState[] {
  return renderHook(() => useAgentStates(loadFixture("issue-to-pr"))).result.current.agents;
}

describe("AgentCard", () => {
  it("renders the developer run from the issue-to-pr fixture", () => {
    const developer = fixtureAgents()[1];
    render(<AgentCard agent={developer} expanded />);

    expect(screen.getByText("developer")).toBeInTheDocument();
    // Task, its nested Grep, Bash and Edit: the count includes the subagent's call.
    expect(screen.getByText(/4 tool calls/)).toBeInTheDocument();
    expect(screen.getByText(/\$0\.05/)).toBeInTheDocument();
    expect(screen.getByText("Added the --verbose flag.")).toBeInTheDocument();
    expect(
      screen.queryByText(/Tool denied by policy/),
      "a run that completed must not keep its denied tool's error as its summary",
    ).toBeNull();
  });

  it("renders the completed check mark, not its escape sequence", () => {
    render(<AgentCard agent={fixtureAgents()[1]} expanded />);
    expect(screen.queryByText(/\\u2713/), "the literal escape must not render (#62)").toBeNull();
    expect(screen.getByText("✓ Completed")).toBeInTheDocument();
  });

  it("shows a failed run's error code after its summary", () => {
    const failed: AgentState = {
      ...fixtureAgents()[1],
      status: "failed",
      summary: "Tool denied by policy: Bash(curl *)",
      errorCode: "ERR_TOOL_DENIED",
    };
    render(<AgentCard agent={failed} />);
    expect(screen.getByText(/Tool denied by policy/)).toBeInTheDocument();
    expect(screen.queryByText(/ERR_TOOL_DENIED/), "a failed card must show its error code").not.toBeNull();
  });

  it("labels a retried run with its attempt", () => {
    render(<AgentCard agent={{ ...fixtureAgents()[1], attempt: 2, retryLabel: "attempt 2" }} />);
    expect(screen.getByText("attempt 2")).toBeInTheDocument();
  });
});
