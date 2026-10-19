// The job's line: a stop per stage in the order the job travels them, the
// line drawn as far as the job has come in its status's colour, the cream
// dot at the stage a running job is in, and the pull request at the end.
import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { JobLine } from "../JobLine";
import type { AgentState, AgentStatus } from "@/hooks/useAgentStates";
import type { LogEvent, PipelineGraph } from "@/lib/types";

const t = "2026-10-19T00:00:00Z";

/** A card as useAgentStates makes it: a run has events, a graph step has none. */
function card(node: string, status: AgentStatus, extra: Partial<AgentState> = {}): AgentState {
  const run = status !== "pending";
  return {
    runId: run ? `run-${node}` : `pending:${node}`,
    node,
    agent: node,
    attempt: run ? 1 : 0,
    status,
    events: run ? [{ type: "agent_started", node, run_id: `run-${node}`, model: "m", max_turns: 1, timestamp: t } as LogEvent] : [],
    toolCalls: [],
    thinkingBlocks: [],
    textBlocks: [],
    costUsd: 0,
    durationMs: 0,
    turnCount: 0,
    toolCallCount: 0,
    summary: "",
    ...extra,
  };
}

function graphOf(...nodes: string[]): PipelineGraph {
  return {
    graph_id: "g",
    name: "g",
    nodes: nodes.map((node_id, i) => ({ node_id, agent_type: node_id, physical_node: node_id, position: { x: i * 100, y: 0 } })),
    edges: [],
    metadata: { max_total_budget_usd: 0, max_total_loops: 0, created_by: "triage" },
  };
}

const stops = () => screen.getAllByTestId("job-line-stop");
const states = () => stops().map((s) => `${s.textContent?.split(",")[0]}:${s.dataset.state}`);
const fill = () => screen.getByTestId("job-progress-fill");

/** matchMedia that answers the reduced-motion and narrow-screen queries. */
function stubMedia({ reduced = false, narrow = false }) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn((query: string) => ({
      matches: query.includes("reduced-motion") ? reduced : query.includes("max-width") ? narrow : false,
      addEventListener() {},
      removeEventListener() {},
    })),
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("JobLine", () => {
  it("carries a running job's cream dot at the stage it is in, the line in the live blue", () => {
    render(
      <JobLine
        agents={[card("triage", "completed"), card("developer", "running"), card("review", "pending")]}
        graph={graphOf("triage", "developer", "review")}
        status="running"
        prUrl={null}
      />,
    );
    expect(states()).toEqual(["triage:done", "developer:flight", "review:pending"]);
    const dot = stops()[1].querySelector(".bg-cream");
    expect(dot, "the stage in flight carries the cream dot").not.toBeNull();
    expect(dot!.className, "the cream dot breathes: the job is alive").toContain("motion-breathe");
    expect(fill().style.background, "the line reads in the running blue, not the gold").toBe("var(--color-info)");
    expect(fill().style.transform, "the line reaches the stage in flight, half way along three stops").toBe("scaleX(0.5)");
  });

  it("stops a waiting job's line at its stages in the warning colour, with nothing breathing", () => {
    // Two stages ran side by side when the job stopped to ask.
    const { container } = render(
      <JobLine
        agents={[card("triage", "completed"), card("developer", "running"), card("docs", "running")]}
        graph={null}
        status="awaiting_input"
        prUrl={null}
      />,
    );
    expect(states()).toEqual(["triage:done", "developer:held", "docs:held"]);
    expect(stops()[1].querySelector(".bg-warning"), "the waiting stage is marked in the warning colour").not.toBeNull();
    expect(container.querySelector(".motion-breathe"), "a job waiting on a person is not in flight").toBeNull();
    expect(fill().style.background).toBe("var(--color-warning)");
    // No graph names the stages ahead, so the way on stays open: three stops and an open end.
    expect(fill().style.transform).toBe(`scaleX(${2 / 3})`);
  });

  it("holds a waiting job at the stage it reached when no stage is running", () => {
    render(<JobLine agents={[card("triage", "completed"), card("review", "completed")]} graph={null} status="awaiting_input" prUrl={null} />);
    expect(states()).toEqual(["triage:done", "review:held"]);
  });

  it("marks a failed job's failed stage in the failure tone, the line muted", () => {
    render(<JobLine agents={[card("triage", "completed"), card("developer", "failed")]} graph={null} status="failed" prUrl={null} />);
    expect(states()).toEqual(["triage:done", "developer:failed"]);
    expect(stops()[1].querySelector(".bg-destructive"), "the failed stage reads as failed").not.toBeNull();
    expect(screen.queryByText("pull request"), "a failed job opened no pull request").toBeNull();
    expect(fill().style.background).toBe("var(--color-fg-muted)");
    expect(fill().style.transform, "a finished line ends at its last stage").toBe("scaleX(1)");
  });

  it("ends a finished job's line at its pull request", () => {
    render(
      <JobLine
        agents={[card("triage", "completed"), card("developer", "completed"), card("review", "completed")]}
        graph={graphOf("triage", "developer", "review")}
        status="pr_merged"
        prUrl="https://forge.example/acme/app/pull/7"
      />,
    );
    expect(states()).toEqual(["triage:done", "developer:done", "review:done", "pull request:pr"]);
    expect(fill().style.background).toBe("var(--color-success)");
    expect(fill().style.transform, "the line runs all the way to the pull request").toBe("scaleX(1)");
  });

  it("puts graph steps that ran without an agent back in the graph's order", () => {
    // useAgentStates lists the run (analyze) first, then the graph steps.
    const onboarding = (node: string, status: AgentStatus, run: boolean) =>
      card(node, status, { agent: "onboarding", runId: run ? `run-${node}` : `node:${node}`, ...(run ? {} : { events: [] }) });
    render(
      <JobLine
        agents={[onboarding("analyze", "running", true), onboarding("init_onboard", "completed", false), onboarding("validate", "pending", false)]}
        graph={graphOf("init_onboard", "analyze", "validate")}
        status="running"
        prUrl={null}
      />,
    );
    expect(states(), "one agent runs every step, so each stop is named by its step").toEqual([
      "init_onboard:done",
      "analyze:flight",
      "validate:pending",
    ]);
  });

  it("keeps a run's place in the journey when the job loops back", () => {
    render(
      <JobLine
        agents={[
          card("triage", "completed"),
          card("developer", "completed"),
          card("review", "completed"),
          card("developer", "running", { runId: "run-developer-2", attempt: 2 }),
        ]}
        graph={graphOf("triage", "developer", "review")}
        status="running"
        prUrl={null}
      />,
    );
    expect(states()).toEqual(["triage:done", "developer:done", "review:done", "developer:flight"]);
  });

  it("names only the stage in flight on a phone", () => {
    stubMedia({ reduced: true, narrow: true });
    render(
      <JobLine
        agents={[card("triage", "completed"), card("developer", "running"), card("review", "pending")]}
        graph={graphOf("triage", "developer", "review")}
        status="running"
        prUrl={null}
      />,
    );
    const shown = stops().map((s) => s.querySelector("[title]")?.textContent ?? null);
    expect(shown).toEqual([null, "developer", null]);
  });

  describe("drawing", () => {
    const running = (reached: number) => [
      card("triage", reached >= 1 ? "completed" : "running"),
      card("developer", reached >= 2 ? "completed" : reached === 1 ? "running" : "pending"),
      card("review", reached === 2 ? "running" : "pending"),
    ];

    function stubAnimate() {
      const animate = vi.fn(() => ({ cancel: vi.fn() }));
      Object.defineProperty(HTMLElement.prototype, "animate", { value: animate, configurable: true, writable: true });
      return animate;
    }

    afterEach(() => {
      delete (HTMLElement.prototype as { animate?: unknown }).animate;
    });

    it("draws from the start once, then draws on the segment a finished stage adds", () => {
      stubMedia({ reduced: false });
      const animate = stubAnimate();
      const graph = graphOf("triage", "developer", "review");
      const { rerender } = render(<JobLine agents={running(1)} graph={graph} status="running" prUrl={null} />);
      expect(animate).toHaveBeenCalledTimes(1);
      expect(animate).toHaveBeenLastCalledWith(
        [{ transform: "scaleX(0)" }, { transform: "scaleX(0.5)" }],
        expect.objectContaining({ duration: 1400 }),
      );

      rerender(<JobLine agents={running(1)} graph={graph} status="running" prUrl={null} />);
      expect(animate, "a render that moves nothing draws nothing").toHaveBeenCalledTimes(1);

      rerender(<JobLine agents={running(2)} graph={graph} status="running" prUrl={null} />);
      expect(animate).toHaveBeenCalledTimes(2);
      expect(animate, "the new segment draws on from where the line stood").toHaveBeenLastCalledWith(
        [{ transform: "scaleX(0.5)" }, { transform: "scaleX(1)" }],
        expect.objectContaining({ duration: 560 }),
      );
    });

    it("shows the line drawn at once under reduced motion", () => {
      stubMedia({ reduced: true });
      const animate = stubAnimate();
      render(<JobLine agents={running(1)} graph={graphOf("triage", "developer", "review")} status="running" prUrl={null} />);
      expect(animate).not.toHaveBeenCalled();
      expect(fill().style.transform).toBe("scaleX(0.5)");
    });
  });
});
