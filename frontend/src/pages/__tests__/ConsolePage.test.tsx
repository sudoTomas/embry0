import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route, useLocation, useParams } from "react-router";
import type {
  ConfigResponse,
  JobFilters,
  JobInput,
  JobListResponse,
  JobResponse,
  QueueResponse,
} from "@/lib/types";
import type { GitHubRepoListResponse } from "@/api/github";
import { makeJob, makeSummary } from "@/components/console/__tests__/fixtures";

// Mock the api boundary (the TasksPage pattern) so the page runs its real
// hooks against controlled responses without touching axios.
const mockFetchJobs = vi.fn<(filters: JobFilters) => Promise<JobListResponse>>();
const mockCreateJob = vi.fn<(req: unknown) => Promise<JobResponse>>();
vi.mock("@/api/jobs", () => ({
  fetchJobs: (filters: JobFilters) => mockFetchJobs(filters),
  fetchJob: vi.fn(),
  createJob: (req: unknown) => mockCreateJob(req),
  runJob: vi.fn(),
  cancelJob: vi.fn(),
}));

const mockFetchQueue = vi.fn<() => Promise<QueueResponse>>();
vi.mock("@/api/queue", () => ({
  fetchQueue: () => mockFetchQueue(),
}));

const mockFetchConfig = vi.fn<() => Promise<ConfigResponse>>();
vi.mock("@/api/config", () => ({
  fetchConfig: () => mockFetchConfig(),
  updateConfig: vi.fn(),
}));

const mockFetchGitHubRepos = vi.fn<() => Promise<GitHubRepoListResponse>>();
vi.mock("@/api/github", () => ({
  fetchGitHubRepos: () => mockFetchGitHubRepos(),
}));

const mockFetchJobInputs = vi.fn<(jobId: string) => Promise<JobInput[]>>();
vi.mock("@/api/inputs", () => ({
  fetchJobInputs: (jobId: string) => mockFetchJobInputs(jobId),
  answerInput: vi.fn(),
  rejectInput: vi.fn(),
  fetchIssueInputs: vi.fn(),
}));

// The RunningCard's WS hook has its own tests — stub it here so the board
// renders without a live connection.
vi.mock("@/hooks/useLiveJobSummary", () => ({
  useLiveJobSummary: vi.fn(),
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

import { useLiveJobSummary } from "@/hooks/useLiveJobSummary";
import { ConsolePage } from "../ConsolePage";

/** Surfaces the router's current URL so tests can assert search-param sync. */
function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname + location.search}</div>;
}

function JobDetailMarker() {
  const { jobId } = useParams();
  return <div data-testid="job-detail">{jobId}</div>;
}

function renderPage(initialEntry = "/console") {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <MemoryRouter initialEntries={[initialEntry]}>
          <LocationProbe />
          <Routes>
            <Route path="/console" element={<ConsolePage />} />
            <Route path="/jobs/:jobId" element={<JobDetailMarker />} />
            <Route path="/jobs" element={<div>jobs history</div>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    ),
  };
}

function jobList(jobs: JobResponse[]): JobListResponse {
  return { jobs, total: jobs.length, offset: 0, limit: 100 };
}

const RECENT = new Date(Date.now() - 3_600_000).toISOString(); // 1h ago

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(useLiveJobSummary).mockReturnValue(makeSummary());
  mockFetchJobs.mockResolvedValue(jobList([]));
  mockFetchQueue.mockResolvedValue({
    depth: 0,
    pending: 0,
    running: 0,
    awaiting_input: 0,
    paused: 0,
  });
  mockFetchConfig.mockResolvedValue({
    max_budget_per_job_usd: 10,
    daily_cap_usd: 100,
    monthly_cap_usd: 1000,
    rate_limit_per_author_per_hour: 10,
    overrun_mode: "soft",
  });
  mockFetchGitHubRepos.mockResolvedValue({
    repos: [
      {
        full_name: "acme/widgets",
        description: null,
        private: false,
        html_url: "https://github.com/acme/widgets",
        default_branch: "main",
        language: null,
        open_issues_count: 0,
      },
    ],
  });
  mockFetchJobInputs.mockResolvedValue([]);
});

describe("ConsolePage — board lanes", () => {
  it("renders all five columns and places cards by status", async () => {
    mockFetchJobs.mockResolvedValue(
      jobList([
        makeJob({ job_id: "run00001aaaa", status: "running", task: "Running task" }),
        makeJob({ job_id: "pend0001aaaa", status: "pending", task: "Queued task" }),
        makeJob({ job_id: "wait0001aaaa", status: "awaiting_input", task: "Blocked task" }),
        makeJob({
          job_id: "done0001aaaa",
          status: "completed",
          task: "Finished task",
          finished_at: RECENT,
        }),
        makeJob({
          job_id: "fail0001aaaa",
          status: "failed",
          task: "Broken task",
          finished_at: RECENT,
          error_code: "ERR_MAX_AGENT_QUESTIONS",
        }),
      ]),
    );
    renderPage();

    // findByText waits out the first-load skeleton; lane placement is then
    // asserted through the column testids.
    const running = screen.getByTestId("board-column-running");
    expect(await within(running).findByText("Running task")).toBeInTheDocument();
    expect(within(screen.getByTestId("board-column-queued")).getByText("Queued task")).toBeInTheDocument();
    expect(within(screen.getByTestId("board-column-needs_you")).getByText("Blocked task")).toBeInTheDocument();
    expect(within(screen.getByTestId("board-column-done")).getByText("Finished task")).toBeInTheDocument();
    expect(within(screen.getByTestId("board-column-failed")).getByText("Broken task")).toBeInTheDocument();
  });

  it("caps the Done/Failed lanes to the last ~24h", async () => {
    mockFetchJobs.mockResolvedValue(
      jobList([
        makeJob({
          job_id: "old00001aaaa",
          status: "completed",
          task: "Ancient finish",
          finished_at: new Date(Date.now() - 48 * 3_600_000).toISOString(),
        }),
        makeJob({
          job_id: "new00001aaaa",
          status: "completed",
          task: "Fresh finish",
          finished_at: RECENT,
        }),
      ]),
    );
    renderPage();

    const done = screen.getByTestId("board-column-done");
    expect(await within(done).findByText("Fresh finish")).toBeInTheDocument();
    expect(within(done).queryByText("Ancient finish")).toBeNull();
    expect(within(done).getByTestId("count-pill")).toHaveTextContent("1");
  });

  it("renders the dispatch-a-job empty state when nothing exists", async () => {
    renderPage();
    expect(await screen.findByText("Nothing running — dispatch a job")).toBeInTheDocument();
  });
});

describe("ConsolePage — URL-synced state", () => {
  it("passes ?status= and ?repo= from the URL into the jobs query", async () => {
    renderPage("/console?status=running&repo=acme/widgets");
    await waitFor(() =>
      expect(mockFetchJobs).toHaveBeenCalledWith(
        expect.objectContaining({ status: "running", repo: "acme/widgets" }),
      ),
    );
  });

  it("writes filter-select changes back to the URL (round-trip)", async () => {
    mockFetchJobs.mockResolvedValue(
      jobList([makeJob({ status: "running", task: "Running task" })]),
    );
    renderPage();
    await screen.findByTestId("board-column-running");

    fireEvent.change(screen.getByLabelText("Statuses"), { target: { value: "failed" } });
    expect(screen.getByTestId("location")).toHaveTextContent("/console?status=failed");
    await waitFor(() =>
      expect(mockFetchJobs).toHaveBeenCalledWith(expect.objectContaining({ status: "failed" })),
    );

    // Clearing the select removes the param entirely.
    fireEvent.change(screen.getByLabelText("Statuses"), { target: { value: "" } });
    expect(screen.getByTestId("location")).toHaveTextContent(/\/console$/);
  });

  it("honors ?label= as a no-op filter until jobs expose labels, with a clearable chip", async () => {
    mockFetchJobs.mockResolvedValue(
      jobList([makeJob({ status: "running", task: "Running task" })]),
    );
    renderPage("/console?label=batch:demo-recruit-v1.4");

    // Jobs without a labels field still render — the filter cannot hide them.
    const running = screen.getByTestId("board-column-running");
    expect(await within(running).findByText("Running task")).toBeInTheDocument();

    const chip = screen.getByTestId("label-filter-chip");
    expect(chip).toHaveTextContent("batch:demo-recruit-v1.4");
    fireEvent.click(chip);
    expect(screen.getByTestId("location")).toHaveTextContent(/\/console$/);
  });

  it("switches tabs through ?tab= and renders the Increment-2 Runs placeholder", async () => {
    mockFetchJobs.mockResolvedValue(
      jobList([makeJob({ status: "running", task: "Running task" })]),
    );
    renderPage();
    await screen.findByTestId("board-column-running");

    fireEvent.click(screen.getByRole("tab", { name: /Runs/ }));
    expect(screen.getByTestId("location")).toHaveTextContent("/console?tab=runs");
    expect(screen.getByText("Runs lands in Increment 2")).toBeInTheDocument();
    expect(screen.queryByTestId("board-column-running")).toBeNull();

    fireEvent.click(screen.getByRole("tab", { name: "Board" }));
    expect(screen.getByTestId("location")).toHaveTextContent("/console?tab=board");
    expect(screen.getByTestId("board-column-running")).toBeInTheDocument();
  });

  it("opens straight onto the Runs placeholder for ?tab=runs deep links", async () => {
    renderPage("/console?tab=runs");
    expect(await screen.findByText("Runs lands in Increment 2")).toBeInTheDocument();
  });
});

describe("ConsolePage — New Job launch flow", () => {
  it("creates a job with repo + task and navigates to its detail page", async () => {
    mockCreateJob.mockResolvedValue(makeJob({ job_id: "new-job-123" }));
    renderPage();
    await screen.findByText("Nothing running — dispatch a job");

    // Header button opens the minimal form (context fixed to git: no
    // pipeline/profile fields exist on it).
    fireEvent.click(screen.getAllByRole("button", { name: /New Job/ })[0]);
    const repoSelect = await screen.findByLabelText("Repository");
    // Repo picker options come from the GitHub listing.
    await waitFor(() => expect(within(repoSelect).getByText("acme/widgets")).toBeInTheDocument());

    fireEvent.change(repoSelect, { target: { value: "acme/widgets" } });
    fireEvent.change(screen.getByLabelText("Task"), {
      target: { value: "Ship the console board" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Dispatch" }));

    await waitFor(() =>
      expect(mockCreateJob).toHaveBeenCalledWith({
        repo: "acme/widgets",
        task: "Ship the console board",
      }),
    );
    // Launch→observe: lands on the new job's detail page.
    expect(await screen.findByTestId("job-detail")).toHaveTextContent("new-job-123");
  });

  it("successful create invalidates the jobs cache", async () => {
    mockCreateJob.mockResolvedValue(makeJob({ job_id: "new-job-456" }));
    const { client } = renderPage();
    await screen.findByText("Nothing running — dispatch a job");
    const invalidate = vi.spyOn(client, "invalidateQueries");

    fireEvent.click(screen.getAllByRole("button", { name: /New Job/ })[0]);
    const repoSelect = await screen.findByLabelText("Repository");
    await waitFor(() => expect(within(repoSelect).getByText("acme/widgets")).toBeInTheDocument());
    fireEvent.change(repoSelect, { target: { value: "acme/widgets" } });
    fireEvent.change(screen.getByLabelText("Task"), { target: { value: "Do the thing" } });
    fireEvent.click(screen.getByRole("button", { name: "Dispatch" }));

    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith(expect.objectContaining({ queryKey: ["jobs"] })),
    );
  });
});
