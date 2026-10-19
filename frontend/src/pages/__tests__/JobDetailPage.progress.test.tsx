// A job's progress bar carries the job's status, so it reads in that
// status's colour: the info blue the standard keeps for running work, the
// amber of "Awaiting input" while the job waits on someone. The gold stays
// for the page's one action, answering.
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { JobResponse } from "@/lib/types";

const job = vi.hoisted(() => ({ current: null as unknown }));
vi.mock("@/hooks/useJobs", () => ({ useJob: () => ({ data: job.current, isLoading: false, isError: false, refetch: vi.fn() }) }));
vi.mock("@/hooks/useInputs", () => ({
  useJobInputs: () => ({ data: [] }),
  useAnswerInput: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock("@/hooks/useTraces", () => ({ useTraces: () => ({ data: { traces: [], total: 0 } }) }));
vi.mock("@/hooks/useJobLogs", () => ({ useJobLogs: () => ({ events: [], isConnected: false, latestCost: 0, pipelineGraph: null }) }));

import { JobDetailPage } from "../JobDetailPage";

function renderJob(overrides: Partial<JobResponse>) {
  job.current = {
    job_id: "job-1234abcd5678", status: "running", repo: "acme/app", task: "Fix the thing", issue_number: null,
    issue_id: null, pr_url: null, error_message: null, total_cost_usd: 0, created_at: "2026-10-18T00:00:00Z",
    started_at: "2026-10-18T00:00:00Z", finished_at: null, ...overrides,
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

describe("the Job page's progress bar", () => {
  it("fills a running job's progress in the live blue, not the gold", () => {
    renderJob({ status: "running" });
    expect(screen.getByTestId("job-progress-fill").style.background).toBe("var(--color-info)");
  });

  it("fills a waiting job's progress in the amber of its status, not the gold", () => {
    renderJob({ status: "awaiting_input" });
    expect(screen.getByTestId("job-progress-fill").style.background).toBe("var(--color-warning)");
  });
});
