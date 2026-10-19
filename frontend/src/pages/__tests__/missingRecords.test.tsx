// A record that does not exist is "not found", with a way back, never a
// load failure with Retry; and a missing job opens no event stream.
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const notFound = Object.assign(new Error("Request failed with status code 404"), {
  response: { status: 404, data: { detail: "Not found" } },
});
const failed = Object.assign(new Error("Request failed with status code 500"), {
  response: { status: 500, data: { detail: "database unavailable" } },
});
const state = vi.hoisted(() => ({ jobError: null as unknown, issueError: null as unknown }));
const useJobLogs = vi.hoisted(() => vi.fn());

vi.mock("@/hooks/useJobs", () => ({
  useJob: () => ({ data: undefined, isLoading: false, isError: !!state.jobError, error: state.jobError, refetch: vi.fn() }),
}));
vi.mock("@/hooks/useInputs", () => ({ useJobInputs: () => ({ data: [] }) }));
vi.mock("@/hooks/useTraces", () => ({ useTraces: () => ({ data: undefined }) }));
vi.mock("@/hooks/useJobLogs", () => ({
  useJobLogs: (id: string | undefined) => {
    useJobLogs(id);
    return { events: [], isConnected: false, isComplete: false, latestCost: 0, pipelineGraph: null };
  },
}));
vi.mock("@/hooks/useIssues", () => ({
  useIssue: () => ({ data: undefined, isLoading: false, isError: !!state.issueError, error: state.issueError, refetch: vi.fn() }),
  useIssueActivity: () => ({ data: [] }),
  useTriageIssue: () => ({ mutate: vi.fn(), isPending: false }),
  useSyncIssue: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteIssue: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateIssue: () => ({ mutate: vi.fn() }),
}));

import { JobDetailPage } from "../JobDetailPage";
import { IssueDetailPage } from "../IssueDetailPage";

function renderAt(path: string) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/jobs/:jobId" element={<JobDetailPage />} />
          <Route path="/issues/:id" element={<IssueDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("a job that does not exist", () => {
  it("says so, links back to the jobs, and streams nothing", () => {
    state.jobError = notFound;
    useJobLogs.mockClear();
    renderAt("/jobs/job-doesnotexist");
    expect(screen.getByText("No job job-doesnotexist")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /All jobs/ })).toHaveAttribute("href", "/jobs");
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
    expect(useJobLogs.mock.calls.at(-1)?.[0], "a missing job must not open its event stream").toBeUndefined();
  });

  it("a job that failed to load offers Retry", () => {
    state.jobError = failed;
    renderAt("/jobs/job-1");
    expect(screen.getByText("Failed to load job")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Retry" })).toBeInTheDocument();
  });
});

describe("an issue that does not exist", () => {
  it("says so and links back to the issues, without Retry", () => {
    state.issueError = notFound;
    renderAt("/issues/iss-doesnotexist");
    expect(screen.getByText("No issue iss-doesnotexist")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /All issues/ })).toHaveAttribute("href", "/issues");
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });
});
