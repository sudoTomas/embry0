import { describe, expect, it, vi } from "vitest";
import { render, renderHook, screen } from "@testing-library/react";

vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => ({ data: [{ type: "developer", display_name: "Developer", avatar: "smith", color: "amber", tagline: "" }], isLoading: false }),
}));

import { AgentCard } from "../AgentCard";
import { useAgentStates, type AgentState } from "@/hooks/useAgentStates";
import { loadFixture } from "@/lib/__tests__/fixtures";
import { inSource } from "@/lib/__tests__/tailwindSource";
import { BUILTIN_IDENTITY, identityOf } from "@/lib/agentIdentity";

function fixtureAgents(): AgentState[] {
  return renderHook(() => useAgentStates(loadFixture("issue-to-pr"))).result.current.agents;
}

describe("AgentCard", () => {
  it("renders the developer run from the issue-to-pr fixture", () => {
    const developer = fixtureAgents()[1];
    render(<AgentCard agent={developer} expanded />);

    expect(screen.getByText("Developer")).toBeInTheDocument();
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

  it.each([false, true])("names the graph node beside an agent that differs from it (expanded: %s)", (expanded) => {
    const qa: AgentState = { ...fixtureAgents()[1], agent: "qa", node: "orchestrate_qa" };
    render(<AgentCard agent={qa} expanded={expanded} />);
    expect(screen.getByText("QA")).toBeInTheDocument();
    expect(screen.queryByText("orchestrate_qa"), "a card whose node differs from its agent must name the node").not.toBeNull();
    expect(screen.getByText("orchestrate_qa").className, "the node id reads as code, not prose").toContain("font-mono");
  });

  it.each([false, true])("shows the name once when the node equals the agent (expanded: %s)", (expanded) => {
    const dev: AgentState = { ...fixtureAgents()[1], agent: "developer", node: "developer" };
    render(<AgentCard agent={dev} expanded={expanded} />);
    expect(screen.getAllByText("Developer"), "agent === node must not repeat the name").toHaveLength(1);
  });

  it.each([false, true])("paints the agent from its identity row (expanded: %s)", (expanded) => {
    const running: AgentState = { ...fixtureAgents()[1], status: "running", agent: "developer", node: "developer" };
    const { container } = render(<AgentCard agent={running} expanded={expanded} />);
    const name = screen.getByText("Developer");
    expect(name, "the name span carries the slug as its title").toHaveAttribute("title", "developer");
    expect(container.querySelector('[data-avatar="smith"]'), "the tile is the row's avatar").not.toBeNull();
  });

  it("draws the sprite in the run's state, not idle", () => {
    const pending: AgentState = { ...fixtureAgents()[1], status: "pending" };
    const { container, unmount } = render(<AgentCard agent={pending} />);
    expect(container.querySelector("svg.sprite"), "compact pending card").toHaveAttribute("data-state", "pending");
    unmount();
    const failed: AgentState = { ...fixtureAgents()[1], status: "failed" };
    const second = render(<AgentCard agent={failed} expanded />);
    expect(second.container.querySelector("svg.sprite"), "expanded failed card").toHaveAttribute("data-state", "failed");
    second.unmount();
    const done = render(<AgentCard agent={fixtureAgents()[1]} expanded />);
    expect(done.container.querySelector("svg.sprite"), "expanded completed card").toHaveAttribute("data-state", "done");
  });

  it("keeps a pending card's sprite out from under any opacity, muting its text instead", () => {
    const pending: AgentState = { ...fixtureAgents()[1], status: "pending", agent: "developer", node: "developer" };
    const { container } = render(<AgentCard agent={pending} />);
    const sprite = container.querySelector("svg.sprite")!;
    expect(sprite).toHaveAttribute("data-state", "pending");
    for (let el = sprite.parentElement; el && el !== container; el = el.parentElement) {
      expect(el.className, "opacity on an ancestor would dim the pending frame").not.toMatch(/(^|\s)opacity-/);
    }
    expect(screen.getByText("Developer").className, "the name is muted while waiting").toContain("text-muted-foreground");
  });

  it("keeps the sprite the only animation in the running card's header", () => {
    const running: AgentState = { ...fixtureAgents()[1], status: "running" };
    const { container } = render(<AgentCard agent={running} expanded />);
    const header = container.firstElementChild!.firstElementChild!;
    expect(header.querySelector("svg.sprite"), "the header is the row holding the sprite").not.toBeNull();
    expect(header).toHaveTextContent("Running");
    expect(header.querySelector('[class*="animate-"]'), "divine rule 9: no second animation beside the sprite").toBeNull();
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

  // The open card's header is ruled off in the card's outline colour. The
  // divider's class was made at runtime by rewriting the outline's, a class
  // Tailwind never generated, so every divider fell to the page's default
  // border instead.
  it.each([...Object.keys(BUILTIN_IDENTITY), "output"])("rules the open %s card's header in its outline colour", (agent) => {
    const { container } = render(<AgentCard agent={{ ...fixtureAgents()[1], agent, node: agent }} expanded />);
    const card = container.firstElementChild!;
    const header = card.firstElementChild!;
    const outline = identityOf(agent).classes.border;
    expect(card.className.split(/\s+/)).toContain(outline);
    expect(header.className.split(/\s+/), `the ${agent} header's divider is not the card's outline`).toContain(outline);
    for (const cls of header.className.split(/\s+/).filter(Boolean)) {
      expect(inSource(cls), `the ${agent} header's ${cls} is in no source file: Tailwind writes no rule for it`).toBe(true);
    }
  });

  it("labels the fixture's retried developer run with its attempt", () => {
    const [first, retry] = fixtureAgents().filter((a) => a.node === "developer");
    render(<AgentCard agent={retry} />);
    expect(screen.getByText("attempt 2")).toBeInTheDocument();
    render(<AgentCard agent={first} />);
    expect(screen.getAllByText(/attempt \d/), "the first attempt carries no label").toHaveLength(1);
  });
});
