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
  verificationOpts: null as unknown,
  attempts: [] as unknown[],
  refetch: vi.fn(),
  refetchInputs: vi.fn(),
  refetchVerification: vi.fn(),
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
vi.mock("@/hooks/useJobVerification", () => ({
  useJobVerification: (_jobId: string, opts: unknown) => { state.verificationOpts = opts; return { data: state.verification, refetch: state.refetchVerification }; },
}));
vi.mock("@/hooks/useAgents", () => ({
  useAgents: () => ({ data: [{ type: "developer", display_name: "Developer", avatar: "smith", color: "amber", skills: [] }] }),
}));
vi.mock("@/api/jobs", () => ({
  resumeJob: vi.fn(), discardJob: vi.fn(),
  fetchAgentSession: vi.fn().mockResolvedValue({ job_id: "j1", agent_type: "developer", mode: "anthropic_api", session_id: null, last_turn_at: null, messages: [{ role: "user", content: "the untruncated text" }] }),
}));
vi.mock("@/components/pipeline/PipelineGraphView", () => ({
  PipelineGraphView: (props: Record<string, unknown>) => (
    <div data-testid="party-map" data-selected={(props.selectedNodeId as string) ?? ""} />
  ),
}));
vi.mock("@/components/qa/QATab", () => ({ QATab: () => <div data-testid="qa-tab" /> }));
vi.mock("@/components/jobs/DeliverablesCard", () => ({ DeliverablesCard: () => null }));

import { JobDetailPage } from "../JobDetailPage";
import { JobLogsPageRedirect } from "@/router/index";
import { fetchAgentSession } from "@/api/jobs";

function job(over: Record<string, unknown> = {}) {
  return {
    job_id: "j1", repo: "owner/repo", task: "Fix it", status: "running", created_at: "2026-10-20T12:00:00Z",
    started_at: "2026-10-20T12:00:00Z", finished_at: null, issue_number: 7, pr_url: null, error_message: null,
    total_cost_usd: 0, pipeline_config: null, ...over,
  };
}

function mount(path = "/jobs/j1") {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // A factory, not one element: React skips a subtree handed the same element object, so a rerender needs a fresh tree.
  const ui = () => (
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/jobs/:jobId" element={<JobDetailPage />} />
          <Route path="/jobs/:jobId/logs" element={<JobLogsPageRedirect />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
  return { ...render(ui()), ui };
}

function use(fixture: FixtureName, extra: LogEvent[] = []) {
  state.events = [...loadFixture(fixture), ...extra];
}

beforeEach(() => {
  state.job = job();
  state.verification = null;
  state.attempts = [];
  state.refetch.mockReset();
  state.refetchInputs.mockReset();
  state.refetchVerification.mockReset();
  use("issue-to-pr");
});

const VERIFICATION_RESULT = { type: "verification_result", tier: "backend_only", gate_verdict: "passed", blocking: false, timestamp: "2026-10-20T12:00:00Z" } as LogEvent;

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

  it("selecting a run without a graph node filters the transcript and highlights no node", () => {
    // A graph is present, but this run's node is not one of its nodes.
    const ghost = { run_id: "run-ghost", node: "ghost", agent: "developer", timestamp: "2026-01-01T00:01:00Z" };
    use("issue-to-pr", [
      { type: "agent_started", ...ghost, model: "m", max_turns: 1 } as LogEvent,
      { type: "turn_start", ...ghost, turn_number: 1 } as LogEvent,
      { type: "text", ...ghost, text: "the ghost speaks" } as LogEvent,
      { type: "agent_completed", ...ghost, is_error: false, error_code: null, error_message: "", output: "", cost_usd: 0, duration_ms: 0, input_tokens: 0, output_tokens: 0, cache_creation_tokens: 0, cache_read_tokens: 0, policy_denials: {}, tools_called: [] } as unknown as LogEvent,
    ]);
    state.job = job({ status: "completed" });
    mount();
    const button = within(screen.getByTestId("party-strip")).getAllByRole("button").find((b) => b.getAttribute("data-run-id") === "run-ghost")!;
    fireEvent.click(button);
    expect(screen.getByTestId("selected-run").getAttribute("data-run-id")).toBe("run-ghost");
    expect(screen.getByTestId("party-map").getAttribute("data-selected"), "no graph node carries this run").toBe("");
    expect(screen.getAllByTestId("run-divider").length, "the record shows this run alone").toBe(1);
    expect(screen.getAllByText("the ghost speaks").length).toBeGreaterThan(0);
    expect(screen.queryByText("Routing to developer then reviewer."), "another run's text is filtered out").toBeNull();
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
    cleanup();
    state.verification = null;
    use("issue-to-pr", [VERIFICATION_RESULT]);
    mount();
    expect(screen.queryByRole("tab", { name: "Verification" }), "the event alone shows the tab").not.toBeNull();
  });

  it("refetches the verification row when the event arrives and once when the job ends, not on every batch", () => {
    const quiet = loadFixture("issue-to-pr");
    state.events = quiet;
    const { rerender, ui } = mount();
    expect(state.refetchVerification, "no event yet: the mount fetch alone").not.toHaveBeenCalled();
    state.events = [...quiet, VERIFICATION_RESULT];
    rerender(ui());
    expect(state.refetchVerification, "the verify node emits the event before it writes the row: fetch it now").toHaveBeenCalledTimes(1);
    state.events = [...quiet, VERIFICATION_RESULT, { type: "text", run_id: "run-fixture-4", node: "review", agent: "reviewer", text: "later", timestamp: "2026-01-01T00:01:00Z" } as LogEvent];
    rerender(ui());
    expect(state.refetchVerification, "a later batch is not a new edge").toHaveBeenCalledTimes(1);
    state.job = job({ status: "completed", finished_at: "2026-10-20T12:05:00Z" });
    rerender(ui());
    expect(state.refetchVerification, "the live-to-terminal transition fetches once more").toHaveBeenCalledTimes(2);
  });

  it("polls for the row only after the event has been seen", () => {
    mount();
    expect(state.verificationOpts, "a live job without the event must not poll").toEqual({ live: true, resultSeen: false });
    cleanup();
    use("issue-to-pr", [VERIFICATION_RESULT]);
    mount();
    expect(state.verificationOpts).toEqual({ live: true, resultSeen: true });
  });

  it("a finished job with the event but no stored row says so plainly", () => {
    use("issue-to-pr", [VERIFICATION_RESULT]);
    state.job = job({ status: "completed", finished_at: "2026-10-20T12:05:00Z" });
    mount();
    fireEvent.click(screen.getByRole("tab", { name: "Verification" }));
    expect(screen.queryByText("No verification report was stored for this job."), "a finished job is not still recording").not.toBeNull();
    expect(screen.queryByText(/being recorded/)).toBeNull();
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

  it("a reviewer run's stored session is read from the review row", async () => {
    // Events spell the review step `reviewer`; nodes/_review.py stores its session as `review`.
    const events = loadFixture("issue-to-pr");
    const review = events.find((e) => e.type === "agent_completed" && e.agent === "reviewer")!.run_id!;
    state.events = events.map((e) => (e.type === "text" && e.run_id === review ? { ...e, truncated: true } : e));
    state.job = job({ status: "completed" });
    mount();
    const offers = screen.getAllByRole("button", { name: /full transcript/i });
    fireEvent.click(offers[offers.length - 1]);
    await screen.findByRole("dialog");
    await waitFor(() => expect(fetchAgentSession, "the drawer must ask for the row the reviewer actually wrote").toHaveBeenCalledWith("j1", "review"));
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

  it("refetches on the interrupt itself, not on every event batch that follows it", () => {
    const events = loadFixture("issue-to-pr"); // carries one interrupt
    state.events = events;
    const { rerender, ui } = mount();
    const calls = state.refetch.mock.calls.length;
    expect(calls, "the interrupt refreshes the job row").toBeGreaterThan(0);
    state.events = [...events, { type: "text", run_id: "run-fixture-4", node: "review", agent: "reviewer", text: "later", timestamp: "2026-01-01T00:01:00Z" } as LogEvent];
    rerender(ui());
    expect(state.refetch.mock.calls.length, "a later event batch must not refetch again").toBe(calls);
    expect(state.refetchInputs.mock.calls.length).toBe(calls);
  });

  it("the ledger is collapsed and holds the traces table", () => {
    mount();
    const ledger = screen.getByTestId("ledger") as HTMLDetailsElement;
    expect(ledger.open).toBe(false);
    expect(ledger.querySelector("summary")?.textContent).toMatch(/ledger/i);
  });
});
