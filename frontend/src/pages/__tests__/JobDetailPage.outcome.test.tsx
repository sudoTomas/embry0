// The Job page shows a finished job's outcome and deliverables whatever
// finished status it reached: a merged PR, a skipped or a partial run too,
// not only "completed".
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { JobResponse } from "@/lib/types";

const job = vi.hoisted(() => ({ current: null as unknown }));
vi.mock("@/hooks/useJobs", () => ({ useJob: () => ({ data: job.current, isLoading: false, isError: false, refetch: vi.fn() }) }));
vi.mock("@/hooks/useInputs", () => ({ useJobInputs: () => ({ data: [], refetch: vi.fn() }) }));
vi.mock("@/hooks/useTraces", () => ({ useTraces: () => ({ data: { traces: [], total: 0 } }) }));
vi.mock("@/hooks/useQaResults", () => ({ useQaAttempts: () => ({ data: [] }), useQaServices: () => ({ data: [] }) }));
vi.mock("@/hooks/useJobVerification", () => ({ useJobVerification: () => ({ data: null, refetch: vi.fn() }) }));
vi.mock("@/hooks/useAgents", () => ({ useAgents: () => ({ data: [] }) }));
vi.mock("@/hooks/useJobLogs", () => ({
  useJobLogs: () => ({ events: [], isConnected: false, isComplete: false, latestCost: 0, latestTokensIn: 0, latestTokensOut: 0, latestTurns: 0, pipelineGraph: null }),
}));
const repos = vi.hoisted(() => ({ current: [] as unknown[] }));
vi.mock("@/hooks/useRepos", () => ({ useRegisteredRepos: () => ({ data: repos.current }) }));
vi.mock("@/components/jobs/DeliverablesCard", () => ({ DeliverablesCard: ({ jobId }: { jobId: string }) => <div data-testid="deliverables">{jobId}</div> }));

import { JobDetailPage } from "../JobDetailPage";

function renderJob(overrides: Partial<JobResponse>) {
  job.current = {
    job_id: "job-1234abcd5678", status: "completed", repo: "acme/app", task: "Fix the thing", issue_number: null,
    issue_id: null, pr_url: null, error_message: null, total_cost_usd: 0, created_at: "2026-10-18T00:00:00Z",
    started_at: "2026-10-18T00:00:00Z", finished_at: "2026-10-18T00:10:00Z", ...overrides,
  };
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={["/jobs/job-1234abcd5678"]}>
        <Routes>
          <Route path="/jobs/:jobId" element={<JobDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  repos.current = [];
});

describe("a finished job's outcome on the Job page", () => {
  it("a merged job shows its success and its deliverables", () => {
    renderJob({ status: "pr_merged", pr_url: "https://forge.example/acme/app/pull/7" });
    expect(screen.getByTestId("deliverables")).toBeInTheDocument();
    expect(screen.getByText(/merged/i, { selector: "div" })).toBeInTheDocument();
  });

  it("a partial job shows its deliverables and why it did not fully pass", () => {
    renderJob({ status: "partial", error_message: "1 of 3 apps failed QA" });
    expect(screen.getByTestId("deliverables")).toBeInTheDocument();
    expect(screen.getByText("1 of 3 apps failed QA")).toBeInTheDocument();
  });

  it("a skipped job shows its deliverables", () => {
    renderJob({ status: "skipped" });
    expect(screen.getByTestId("deliverables")).toBeInTheDocument();
  });

  it("a running job shows no deliverables yet", () => {
    renderJob({ status: "running", finished_at: null });
    expect(screen.queryByTestId("deliverables")).toBeNull();
  });

  it("a finished onboard job says where its test setup went (#29)", () => {
    renderJob({ status: "failed", pipeline_template: "onboard", repo: "group/sub/proj", error_message: "smoke failed" });
    expect(screen.getByText("smoke failed")).toBeInTheDocument();
    expect(screen.getByTestId("onboard-outcome-manual-cta")).toHaveAttribute("href", "/repos/onboard?repo=group%2Fsub%2Fproj");
  });
});

describe("the Job page's issue link", () => {
  it("opens the job's embry0 issue, whatever forge or tracker holds it", () => {
    renderJob({ status: "running", finished_at: null, issue_id: "iss-42", issue_number: 42, repo: "group/sub/project" });
    const link = screen.getByRole("link", { name: /Issue #42/ });
    expect(link).toHaveAttribute("href", "/issues/iss-42");
  });

  it("shows no issue link for a job with no repository", () => {
    renderJob({ status: "running", finished_at: null, issue_id: null, issue_number: 7, repo: null });
    expect(screen.queryByRole("link", { name: /Issue #7/ })).toBeNull();
    expect(document.querySelector('a[href*="github.com"]')).toBeNull();
  });

  // A job made through POST /jobs (the Create job dialog, the MCP job_create
  // tool) carries an issue number but no issue row: it links to the issue on
  // its own forge, read from the pull request's URL or the repository's row.
  it("links an issue-less GitHub job to the issue on GitHub", () => {
    renderJob({
      status: "pr_merged", issue_id: null, issue_number: 651, repo: "acme/app",
      pr_url: "https://github.com/acme/app/pull/700",
    });
    const link = screen.getByRole("link", { name: /Issue #651/ });
    expect(link).toHaveAttribute("href", "https://github.com/acme/app/issues/651");
  });

  it("links an issue-less GitLab job to the issue on its own GitLab host", () => {
    renderJob({
      status: "completed", issue_id: null, issue_number: 12, repo: "group/sub/project",
      pr_url: "https://gitlab.example.com/group/sub/project/-/merge_requests/3",
    });
    const link = screen.getByRole("link", { name: /Issue #12/ });
    expect(link).toHaveAttribute("href", "https://gitlab.example.com/group/sub/project/-/issues/12");
    expect(document.querySelector('a[href*="github.com"]')).toBeNull();
  });

  it("links an issue-less job with no pull request by its registered repository's forge", () => {
    repos.current = [{ repo: "acme/app", forge: "github" }];
    renderJob({ status: "running", finished_at: null, issue_id: null, issue_number: 9, repo: "acme/app" });
    expect(screen.getByRole("link", { name: /Issue #9/ })).toHaveAttribute("href", "https://github.com/acme/app/issues/9");
  });

  it("shows no issue link where the forge's address is unknown", () => {
    // A GitLab instance can be self-managed: without a merge request URL its
    // host is unknown here, and a guessed link would lead nowhere.
    repos.current = [{ repo: "group/project", forge: "gitlab" }];
    renderJob({ status: "running", finished_at: null, issue_id: null, issue_number: 5, repo: "group/project" });
    expect(screen.queryByRole("link", { name: /Issue #5/ })).toBeNull();
    renderJob({ status: "running", finished_at: null, issue_id: null, issue_number: 6, repo: "acme/unregistered" });
    expect(screen.queryByRole("link", { name: /Issue #6/ })).toBeNull();
  });
});

describe("the Job page's back arrow", () => {
  it("is one named link, not a button inside a link", () => {
    const { container } = renderJob({ status: "running", finished_at: null });
    expect(screen.getByRole("link", { name: "Back to jobs" })).toHaveAttribute("href", "/jobs");
    expect(container.querySelector("a button")).toBeNull();
  });
});
