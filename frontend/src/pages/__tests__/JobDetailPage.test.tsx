import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within, fireEvent, waitFor, cleanup } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { loadFixture, type FixtureName } from "@/lib/__tests__/fixtures";
import { reduceTotals } from "@/lib/eventTotals";
import type { LogEvent } from "@/lib/types";

const state = vi.hoisted(() => ({
  job: {} as Record<string, unknown>,
  events: [] as LogEvent[],
  verification: null as unknown,
  attempts: [] as unknown[],
  refetch: vi.fn(),
  refetchInputs: vi.fn(),
  graphProps: [] as unknown[],
}));

vi.mock("@/hooks/useJobs", () => ({
  useJob: () => ({ data: state.job, isLoading: false, isError: false, refetch: state.refetch }),
}));
vi.mock("@/hooks/useJobLogs", () => ({
  useJobLogs: () => {
    const totals = reduceTotals(state.events);
    const graph = state.events.find((e) => e.type === "pipeline_graph");
    return {
      events: state.events, isConnected: true, isComplete: false,
      latestCost: totals.costUsd, latestTokensIn: totals.tokensIn, latestTokensOut: totals.tokensOut, latestTurns: totals.turns,
      pipelineGraph: graph && graph.type === "pipeline_graph" ? graph.graph : null,
    };
  },
}));
vi.mock("@/hooks/useInputs", () => ({ useJobInputs: () => ({ data: [], refetch: state.refetchInputs }) }));
vi.mock("@/hooks/useTraces", () => ({ useTraces: () => ({ data: { traces: [], total: 0 } }) }));
vi.mock("@/hooks/useQaResults", () => ({
  useQaAttempts: () => ({ data: state.attempts, isLoading: false }),
  useQaServices: () => ({ data: [] }),
}));
vi.mock("@/hooks/useJobVerification", () => ({ useJobVerification: () => ({ data: state.verification }) }));
vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => ({ data: [{ type: "developer", display_name: "Developer", avatar: "smith", color: "amber", skills: [] }] }),
}));
vi.mock("@/api/jobs", () => ({
  resumeJob: vi.fn(), discardJob: vi.fn(),
  fetchAgentSession: vi.fn().mockResolvedValue({ job_id: "j1", agent_type: "developer", mode: "anthropic_api", session_id: null, last_turn_at: null, messages: [{ role: "user", content: "the untruncated text" }] }),
}));
vi.mock("@/components/pipeline/PipelineGraphView", () => ({
  PipelineGraphView: (props: Record<string, unknown>) => {
    state.graphProps.push(props);
    return <div data-testid="party-map" data-selected={(props.selectedNodeId as string) ?? ""} />;
  },
}));
vi.mock("@/components/qa/QATab", () => ({ QATab: () => <div data-testid="qa-tab" /> }));
vi.mock("@/components/jobs/DeliverablesCard", () => ({ DeliverablesCard: () => null }));

import { JobDetailPage } from "../JobDetailPage";
import { JobLogsPageRedirect } from "@/router/index";

function job(over: Record<string, unknown> = {}) {
  return {
    job_id: "j1", repo: "owner/repo", task: "Fix it", status: "running", created_at: "2026-10-20T12:00:00Z",
    started_at: "2026-10-20T12:00:00Z", finished_at: null, issue_number: 7, pr_url: null, error_message: null,
    total_cost_usd: 0, pipeline_config: null, ...over,
  };
}

function mount(path = "/jobs/j1") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/jobs/:jobId" element={<JobDetailPage />} />
          <Route path="/jobs/:jobId/logs" element={<JobLogsPageRedirect />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function use(fixture: FixtureName, extra: LogEvent[] = []) {
  state.events = [...loadFixture(fixture), ...extra];
}

beforeEach(() => {
  state.job = job();
  state.verification = null;
  state.attempts = [];
  state.graphProps = [];
  state.refetch.mockReset();
  state.refetchInputs.mockReset();
  use("issue-to-pr");
});

describe("JobDetailPage", () => {
  it("/jobs/:id/logs redirects to the job page", () => {
    mount("/jobs/j1/logs");
    expect(screen.getByTestId("party-strip"), "the logs route must land on the one job page").toBeTruthy();
    expect(screen.queryByText("Agent Logs")).toBeNull();
  });

  it("the party strip lists the runs in order and the HUD shows the stream totals", () => {
    state.job = job({ status: "completed", finished_at: "2026-10-20T12:05:00Z" });
    mount();
    const strip = screen.getByTestId("party-strip");
    const ids = within(strip).getAllByRole("button").map((b) => b.getAttribute("data-run-id"));
    const started = state.events.filter((e) => e.type === "agent_started").map((e) => e.run_id);
    expect(ids.slice(0, started.length)).toEqual(started);
    const totals = reduceTotals(state.events);
    const hud = screen.getByTestId("job-hud");
    expect(hud.querySelector("[data-hud='turns']")?.textContent).toBe(String(totals.turns));
    expect(hud.querySelector("[data-hud='tokens-in']")?.textContent).toBe(totals.tokensIn.toLocaleString());
    expect(hud.querySelector("[data-hud='status']")?.textContent).toBe("completed");
    expect(hud.querySelector("[data-hud='elapsed']")?.textContent).toBe("5m 0s");
  });

  it("the budget meter reads pipeline_config.max_budget_usd", () => {
    state.job = job({ pipeline_config: { max_budget_usd: 2 } });
    mount();
    expect(screen.getByRole("meter").getAttribute("aria-valuemax")).toBe("2");
  });

  it("selecting a run on the strip filters the conversation and highlights its node", async () => {
    mount();
    const buttons = within(screen.getByTestId("party-strip")).getAllByRole("button");
    fireEvent.click(buttons[0]);
    const runId = buttons[0].getAttribute("data-run-id")!;
    await waitFor(() => expect(screen.getByTestId("party-map").getAttribute("data-selected")).not.toBe(""));
    const dividers = screen.getAllByTestId("run-divider");
    expect(dividers.length, "one run selected shows one divider").toBe(1);
    expect(screen.getByTestId("selected-run").getAttribute("data-run-id")).toBe(runId);
  });

  it("selecting a run without a graph node filters the transcript and highlights no node", async () => {
    use("legacy-rows");
    state.job = job({ status: "completed" });
    mount();
    const buttons = within(screen.getByTestId("party-strip")).getAllByRole("button");
    fireEvent.click(buttons[0]);
    expect(screen.getByTestId("selected-run")).toBeTruthy();
    expect(screen.queryByTestId("party-map"), "no graph, no map").toBeNull();
  });

  it("a legacy job shows the strip and hides the map", () => {
    use("legacy-rows");
    state.job = job({ status: "completed" });
    mount();
    expect(screen.getByTestId("party-strip")).toBeTruthy();
    expect(screen.queryByTestId("party-map")).toBeNull();
  });

  it("the QA tab is keyed on qa_apps_resolved or attempts, not the template name", () => {
    state.job = job({ pipeline_template: "qa" });
    mount();
    expect(screen.queryByRole("tab", { name: "QA" }), "a template named qa with no QA events shows no tab").toBeNull();
    use("qa");
    mount();
    expect(screen.getAllByRole("tab", { name: "QA" }).length).toBeGreaterThan(0);
  });

  it("the Verification tab appears from a row or a verification_result event, and hides otherwise", () => {
    mount();
    expect(screen.queryByRole("tab", { name: "Verification" })).toBeNull();
    state.verification = { job_id: "j1", repo: "o/r", mode: "on", tier: "backend_only", gate_verdict: "passed", blocking: false, browser_qa: "skipped", human_merge_only: false, base_sha: "a".repeat(40), head_sha: "b".repeat(40), report: { gate: { targets: [] } }, published_at: null, created_at: "", updated_at: "" };
    mount();
    fireEvent.click(screen.getAllByRole("tab", { name: "Verification" })[0]);
    expect(screen.getByTestId("verification-tab")).toBeTruthy();
    state.verification = null;
    use("issue-to-pr", [{ type: "verification_result", tier: "backend_only", gate_verdict: "passed", blocking: false, timestamp: "2026-10-20T12:00:00Z" } as LogEvent]);
    mount();
    expect(screen.getAllByRole("tab", { name: "Verification" }).length, "the event alone shows the tab").toBeGreaterThan(0);
  });

  it("opens the transcript drawer for a truncated finished run", async () => {
    const events = loadFixture("issue-to-pr");
    const run = events.find((e) => e.type === "agent_completed")!.run_id!;
    state.events = events.map((e) => (e.type === "text" && e.run_id === run ? { ...e, truncated: true } : e));
    state.job = job({ status: "completed" });
    mount();
    fireEvent.click(screen.getByRole("button", { name: /full transcript/i }));
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(dialog.textContent).toContain("the untruncated text"));
  });

  it("refetches the job and its inputs when the stream reports a pause for input", () => {
    // The fixture carries an interrupt, which also triggers the refresh; strip it so the pause is the only trigger.
    const quiet = loadFixture("issue-to-pr").filter((e) => e.type !== "interrupt");
    state.events = quiet;
    mount();
    expect(state.refetch, "no pause and no interrupt: the poll alone").not.toHaveBeenCalled();
    cleanup();
    state.events = [...quiet, { type: "agent_paused_for_input", count: 1, timestamp: "2026-10-20T12:00:00Z", run_id: "r-x", node: "developer", agent: "developer" } as LogEvent];
    mount();
    expect(state.refetch, "the banner must not wait for the 10 s poll").toHaveBeenCalled();
    expect(state.refetchInputs).toHaveBeenCalled();
  });

  it("the ledger is collapsed and holds the traces table", () => {
    mount();
    const ledger = screen.getByTestId("ledger") as HTMLDetailsElement;
    expect(ledger.open).toBe(false);
    expect(ledger.querySelector("summary")?.textContent).toMatch(/ledger/i);
  });
});
