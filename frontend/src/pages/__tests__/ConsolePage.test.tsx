import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { act, render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Routes, Route, useLocation, useParams } from "react-router";
import type {
  JobFilters,
  JobInput,
  JobListResponse,
  JobResponse,
  QueueResponse,
} from "@/lib/types";
import type { GitHubRepoListResponse } from "@/api/github";
import type { EffectiveSettings } from "@/lib/types/settings";
import { makeJob, makeSummary } from "@/components/console/__tests__/fixtures";

// Mock the api boundary (the TasksPage pattern) so the page runs its real
// hooks against controlled responses without touching axios.
const mockFetchJobs = vi.fn<(filters: JobFilters) => Promise<JobListResponse>>();
const mockCreateJob = vi.fn<(req: unknown) => Promise<JobResponse>>();
vi.mock("@/api/jobs", () => ({
  fetchJobs: (filters: JobFilters) => mockFetchJobs(filters),
  fetchJob: vi.fn(),
  createJob: (req: unknown) => mockCreateJob(req),
  cancelJob: vi.fn(),
}));

const mockFetchQueue = vi.fn<() => Promise<QueueResponse>>();
vi.mock("@/api/queue", () => ({
  fetchQueue: () => mockFetchQueue(),
}));

// The per-job budget the running cards show is the settings document's budget section (#171).
const mockFetchEffectiveSettings = vi.fn<() => Promise<EffectiveSettings>>();
vi.mock("@/api/settings", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/settings")>()),
  fetchEffectiveSettings: () => mockFetchEffectiveSettings(),
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
  mockFetchEffectiveSettings.mockResolvedValue({
    generation: 1,
    unknown_sections: [],
    unknown_keys: [],
    sections: [
      {
        name: "budget",
        summary: "",
        keys: Object.entries({
          max_budget_per_job_usd: 10,
          daily_cap_usd: 100,
          monthly_cap_usd: 1000,
          rate_limit_per_author_per_hour: 10,
          overrun_mode: "soft",
        }).map(([key, value]) => ({
          key,
          value,
          default: value,
          source: "default" as const,
          set_by: null,
          set_at: null,
          env: null,
          env_still_set: false,
          overridable: [],
          refused: null,
          description: "",
        })),
      },
    ],
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
    // asserted through the column testids, read after it: the first paint
    // draws the lanes anew.
    await screen.findByText("Running task");
    expect(within(screen.getByTestId("board-column-running")).getByText("Running task")).toBeInTheDocument();
    expect(within(screen.getByTestId("board-column-queued")).getByText("Queued task")).toBeInTheDocument();
    expect(within(screen.getByTestId("board-column-needs_you")).getByText("Blocked task")).toBeInTheDocument();
    expect(within(screen.getByTestId("board-column-done")).getByText("Finished task")).toBeInTheDocument();
    expect(within(screen.getByTestId("board-column-failed")).getByText("Broken task")).toBeInTheDocument();
  });

  it("gives the Needs-you lane twice a lane's share on a wide screen while an agent waits", async () => {
    mockFetchJobs.mockResolvedValue(
      jobList([
        makeJob({ job_id: "wait0001aaaa", status: "awaiting_input", task: "Blocked task" }),
        makeJob({ job_id: "run00001aaaa", status: "running", task: "Running task" }),
      ]),
    );
    renderPage();
    await screen.findByText("Running task");
    // A fifth of the board left the agent's question a few words a line.
    // The other lanes keep the width their cards' header rows need.
    const board = screen.getByTestId("board-column-needs_you").parentElement!;
    expect(board).toHaveClass("xl:grid-cols-[minmax(0,2fr)_repeat(4,minmax(11.5rem,1fr))]");
    expect(board).not.toHaveClass("xl:grid-cols-5");
  });

  it("keeps the lanes equal while no agent waits", async () => {
    mockFetchJobs.mockResolvedValue(
      jobList([makeJob({ job_id: "run00001aaaa", status: "running", task: "Running task" })]),
    );
    renderPage();
    await screen.findByText("Running task");
    expect(screen.getByTestId("board-column-needs_you").parentElement).toHaveClass("xl:grid-cols-5");
  });

  it("still renders the board when a job carries a status this build does not know", async () => {
    mockFetchJobs.mockResolvedValue(
      jobList([
        makeJob({ job_id: "run00001aaaa", status: "running", task: "Running task" }),
        makeJob({ job_id: "new00001aaaa", status: "reverted" as JobResponse["status"], task: "Newer task", finished_at: RECENT }),
      ]),
    );
    renderPage();
    await screen.findByText("Running task");
    expect(within(screen.getByTestId("board-column-running")).getByText("Running task")).toBeInTheDocument();
    expect(within(screen.getByTestId("board-column-failed")).getByText("Newer task")).toBeInTheDocument();
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

    await screen.findByText("Fresh finish");
    const done = screen.getByTestId("board-column-done");
    expect(within(done).getByText("Fresh finish")).toBeInTheDocument();
    expect(within(done).queryByText("Ancient finish")).toBeNull();
    expect(within(done).getByTestId("count-pill")).toHaveTextContent("1");
  });

  it("renders the dispatch-a-job empty state when nothing exists", async () => {
    renderPage();
    expect(await screen.findByText("Nothing running — dispatch a job")).toBeInTheDocument();
  });
});

describe("ConsolePage — the first paint", () => {
  const blocked = makeJob({ job_id: "wait0001aaaa", status: "awaiting_input", task: "Blocked task" });
  const running = makeJob({ job_id: "run00001aaaa", status: "running", task: "Running task" });
  const question: JobInput = {
    id: "in-1",
    issue_id: null,
    job_id: "wait0001aaaa",
    asking_node: "triage",
    question: "Which branch should this land on?",
    importance: "blocking",
    auto_answer: null,
    answer: null,
    answered_by: null,
    status: "pending",
    created_at: RECENT,
    answered_at: null,
  };

  it("holds the lanes until a Needs-you job's question is in, then draws them anew", async () => {
    mockFetchJobs.mockResolvedValue(jobList([blocked, running]));
    let answer: (inputs: JobInput[]) => void = () => {};
    mockFetchJobInputs.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    renderPage();

    // The jobs are in and the lanes count them, but no card is drawn: the
    // question would grow its card and push the lanes below it down.
    await waitFor(() =>
      expect(within(screen.getByTestId("board-column-running")).getByTestId("count-pill")).toHaveTextContent("1"),
    );
    expect(screen.queryByText("Running task")).toBeNull();
    const heldLane = screen.getByTestId("board-column-running");

    await act(async () => answer([question]));
    expect(await screen.findByText("Running task")).toBeInTheDocument();
    expect(screen.getByTestId("needs-you-question")).toHaveTextContent("Which branch should this land on?");
    // New lanes, not the skeleton lanes moved: a layout shift counts the
    // nodes that stay and move.
    expect(screen.getByTestId("board-column-running")).not.toBe(heldLane);
  });

  it("does not hold the lanes on a question fetch that fails", async () => {
    mockFetchJobs.mockResolvedValue(jobList([blocked, running]));
    mockFetchJobInputs.mockRejectedValue(new Error("boom"));
    renderPage();
    expect(await screen.findByText("Running task")).toBeInTheDocument();
    expect(screen.getByTestId("needs-you-question")).toHaveTextContent("Pipeline paused");
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

  it("renders no Runs tab and no label chip (RAV-1058 IA sweep)", async () => {
    mockFetchJobs.mockResolvedValue(
      jobList([makeJob({ status: "running", task: "Running task" })]),
    );
    renderPage("/console?label=batch:demo-recruit-v1.4&tab=runs");
    // Board renders directly — the ?tab=/?label= params are simply ignored.
    await screen.findByText("Running task");
    expect(within(screen.getByTestId("board-column-running")).getByText("Running task")).toBeInTheDocument();
    expect(screen.queryByRole("tab")).toBeNull();
    expect(screen.queryByTestId("label-filter-chip")).toBeNull();
    expect(screen.queryByText(/Increment 2/)).toBeNull();
  });
});

describe("ConsolePage — on a phone", () => {
  it("shares the filter line in halves, so a long repo name stays on screen", async () => {
    mockFetchJobs.mockResolvedValue(
      jobList([makeJob({ status: "running", task: "Running task", repo: "acme/a-repository-name-longer-than-a-phone-is-wide" })]),
    );
    renderPage();
    await screen.findByText("Running task");
    // A select is as wide as its longest option, and as a flex item it did
    // not shrink below that: it pushed main sideways. Wrapping instead would
    // move the board down when the jobs poll brought a long name.
    for (const label of ["Statuses", "Repos"]) {
      expect(screen.getByLabelText(label)).toHaveClass("min-w-0", "max-w-full", "flex-1", "sm:flex-initial");
    }
    expect(screen.getByLabelText("Repos").parentElement).not.toHaveClass("flex-wrap");
  });
});

describe("ConsolePage — the one gold action", () => {
  const newJob = () => screen.getAllByRole("button", { name: /New job/ })[0];

  it("holds New job in the secondary tone until the jobs say whether an agent waits", async () => {
    let answer: (list: JobListResponse) => void = () => {};
    mockFetchJobs.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    renderPage();

    // Gold before the jobs were in went out again the moment they showed
    // an agent waiting, whose answer is the page's one gold action.
    expect(newJob()).toHaveClass("bg-overlay");
    expect(newJob()).not.toHaveClass("bg-primary");
    expect(screen.getByTestId("heartbeat-badge")).toHaveTextContent("Connecting…");
    expect(screen.getByTestId("heartbeat-badge")).not.toHaveTextContent("OFFLINE");

    await act(async () => answer(jobList([makeJob({ status: "running", task: "Running task" })])));
    await waitFor(() => expect(newJob()).toHaveClass("bg-primary"));
    expect(screen.getByTestId("heartbeat-badge")).toHaveTextContent(/Updated \d+s ago/);
  });

  it("leaves New job secondary once the jobs show an agent waiting", async () => {
    mockFetchJobs.mockResolvedValue(
      jobList([makeJob({ job_id: "wait0001aaaa", status: "awaiting_input", task: "Blocked task" })]),
    );
    renderPage();
    await screen.findByText("Blocked task");
    expect(newJob()).toHaveClass("bg-overlay");
  });

  it("leaves New job secondary and reads OFFLINE when the jobs fail to load", async () => {
    mockFetchJobs.mockRejectedValue(new Error("unreachable"));
    renderPage();
    await screen.findByText("Failed to load jobs");
    expect(newJob()).toHaveClass("bg-overlay");
    expect(screen.getByTestId("heartbeat-badge")).toHaveTextContent("OFFLINE");
  });
});

describe("ConsolePage — New Job launch flow", () => {
  it("creates a job with repo + task and navigates to its detail page", async () => {
    mockCreateJob.mockResolvedValue(makeJob({ job_id: "new-job-123" }));
    renderPage();
    await screen.findByText("Nothing running — dispatch a job");

    // Header button opens the minimal form (context fixed to git: no
    // pipeline/profile fields exist on it).
    fireEvent.click(screen.getAllByRole("button", { name: /New job/ })[0]);
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

    fireEvent.click(screen.getAllByRole("button", { name: /New job/ })[0]);
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

describe("ConsolePage — motion when a poll moves a job", () => {
  const LANE_ORDER = ["needs_you", "queued", "running", "done", "failed"];
  let animated: { el: HTMLElement; keyframes: Keyframe[] }[] = [];
  /** Cards travelling, as opposed to counts ticking. */
  const glides = () => animated.filter((a) => String(a.keyframes[0].transform).startsWith("translate("));
  const ticked = () =>
    animated.filter((a) => String(a.keyframes[0].transform).startsWith("scale(")).map((a) => a.el);

  /** jsdom lays nothing out: a card's place is its lane (300 px apart) and
   * its turn within the lane (120 px apart). */
  function placeOnBoard(this: HTMLElement): DOMRect {
    const lane = this.closest("[data-testid^='board-column-']")?.getAttribute("data-testid");
    const col = LANE_ORDER.indexOf(lane?.slice("board-column-".length) ?? "");
    const row = this.parentElement ? Array.from(this.parentElement.children).indexOf(this) : 0;
    return { left: col * 300, top: row * 120 } as DOMRect;
  }

  /** The GlideItem around the card showing `text`. */
  const glideItemOf = (text: string) =>
    screen.getByText(text).closest("[role='link']")?.parentElement as HTMLElement;

  beforeEach(() => {
    animated = [];
    vi.stubGlobal(
      "matchMedia",
      vi.fn(() => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} })),
    );
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(placeOnBoard);
    HTMLElement.prototype.animate = function (this: HTMLElement, keyframes: Keyframe[]) {
      animated.push({ el: this, keyframes });
      return { cancel: () => {} } as unknown as Animation;
    } as HTMLElement["animate"];
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
    delete (HTMLElement.prototype as Partial<HTMLElement>).animate;
  });

  it("glides a job's card to its new lane when a poll moves it, ticking both counts", async () => {
    mockFetchJobs.mockResolvedValue(
      jobList([makeJob({ job_id: "move0001aaaa", status: "pending", task: "Moving task" })]),
    );
    const { client } = renderPage();
    await screen.findByText("Moving task");
    expect(within(screen.getByTestId("board-column-queued")).getByText("Moving task")).toBeInTheDocument();
    expect(animated).toEqual([]);

    mockFetchJobs.mockResolvedValue(
      jobList([makeJob({ job_id: "move0001aaaa", status: "running", task: "Moving task" })]),
    );
    await client.invalidateQueries({ queryKey: ["jobs"] });
    expect(await within(screen.getByTestId("board-column-running")).findByText("Moving task")).toBeInTheDocument();

    expect(glides()).toHaveLength(1);
    expect(glides()[0].el).toBe(glideItemOf("Moving task"));
    expect(glides()[0].keyframes[0]).toEqual({ transform: "translate(-300px, 0px)" });
    expect(ticked()).toEqual([
      within(screen.getByTestId("board-column-queued")).getByTestId("count-pill"),
      within(screen.getByTestId("board-column-running")).getByTestId("count-pill"),
    ]);
  });

  it("draws the first board in place: no lane glides from where its skeleton stood", async () => {
    // While skeletons stand in the lanes, everything sits 500 px lower, as
    // the lanes below a stack of skeletons do on a phone.
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
      () => ({ left: 0, top: document.querySelector(".animate-pulse") ? 500 : 0 }) as DOMRect,
    );
    mockFetchJobs.mockResolvedValue(jobList([makeJob({ status: "pending", task: "Queued task" })]));
    renderPage();
    await screen.findByText("Queued task");
    expect(glides()).toEqual([]);
  });

  it("glides nothing and ticks no count when a filter change reflows the board", async () => {
    const first = makeJob({ job_id: "one00001aaaa", repo: "acme/one", task: "First task" });
    const second = makeJob({ job_id: "two00001aaaa", repo: "acme/two", task: "Second task" });
    mockFetchJobs.mockImplementation(async (filters) =>
      jobList(filters.repo ? [second] : [first, second]),
    );
    renderPage();
    await screen.findByText("Second task");

    fireEvent.change(screen.getByLabelText("Repos"), { target: { value: "acme/two" } });
    await waitFor(() => expect(screen.queryByText("First task")).toBeNull());
    await screen.findByText("Second task");

    // Back to every repo: the cached board returns at once, and the second
    // card sits one place lower than it did a moment ago.
    animated = [];
    fireEvent.change(screen.getByLabelText("Repos"), { target: { value: "" } });
    await screen.findByText("First task");

    expect(animated).toEqual([]);
  });
});
