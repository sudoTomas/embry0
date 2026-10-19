// The Job page shows a finished job's outcome and deliverables whatever
// finished status it reached: a merged PR, a skipped or a partial run too,
// not only "completed".
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { JobResponse } from "@/lib/types";

const job = vi.hoisted(() => ({ current: null as unknown }));
vi.mock("@/hooks/useJobs", () => ({ useJob: () => ({ data: job.current, isLoading: false, isError: false, refetch: vi.fn() }) }));
vi.mock("@/hooks/useInputs", () => ({ useJobInputs: () => ({ data: [] }) }));
vi.mock("@/hooks/useTraces", () => ({ useTraces: () => ({ data: { traces: [], total: 0 } }) }));
vi.mock("@/hooks/useJobLogs", () => ({ useJobLogs: () => ({ events: [], isConnected: false, latestCost: 0, pipelineGraph: null }) }));
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
});

describe("the Job page's issue link", () => {
  it("opens the job's embry0 issue, whatever forge or tracker holds it", () => {
    renderJob({ status: "running", finished_at: null, issue_id: "iss-42", issue_number: 42, repo: "group/sub/project" });
    const link = screen.getByRole("link", { name: /Issue #42/ });
    expect(link).toHaveAttribute("href", "/issues/iss-42");
  });

  it("never builds a github.com link, and shows no link without an issue row", () => {
    renderJob({ status: "running", finished_at: null, issue_id: null, issue_number: 7, repo: null });
    expect(screen.queryByRole("link", { name: /Issue #7/ })).toBeNull();
    expect(document.querySelector('a[href*="github.com"]')).toBeNull();
  });
});
