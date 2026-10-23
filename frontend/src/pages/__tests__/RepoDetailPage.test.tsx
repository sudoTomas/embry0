import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockFetchRepoDetail = vi.fn();
vi.mock("@/api/repos", () => ({
  fetchRepoDetail: (...a: unknown[]) => mockFetchRepoDetail(...a),
}));

const mockUseRepoEnv = vi.fn();
const mockUseResolvedRepoEnv = vi.fn();
vi.mock("@/hooks/useEnvironments", () => ({
  useRepoEnv: () => mockUseRepoEnv(),
  useResolvedRepoEnv: (repo: string, enabled: boolean) => mockUseResolvedRepoEnv(repo, enabled),
}));

const mockUseQaRunsForRepo = vi.fn();
vi.mock("@/hooks/useQaDashboard", () => ({
  useQaRunsForRepo: () => mockUseQaRunsForRepo(),
}));

vi.mock("@/components/qa/QaConfigEditor", () => ({
  QaConfigEditor: ({ repo }: { repo: string }) => <div data-testid="qa-config-editor">{repo}</div>,
}));

vi.mock("@/components/environments/RepoPreferencesSection", () => ({
  RepoPreferencesSection: () => <div data-testid="repo-prefs-section" />,
}));
vi.mock("@/components/environments/PullRequestReviewCard", () => ({
  PullRequestReviewCard: ({ repo }: { repo: string }) => <div data-testid="pr-review-card">{repo}</div>,
}));

vi.mock("@/components/repos/RepoAccountsSection", () => ({
  RepoAccountsSection: ({ repo, forge }: { repo: string; forge: string }) => (
    <div data-testid="repo-accounts-section">{`${repo} ${forge}`}</div>
  ),
}));

vi.mock("@/components/repos/RepoDockerAccessSection", () => ({
  RepoDockerAccessSection: ({ repo, dockerAccess }: { repo: string; dockerAccess: boolean }) => (
    <div data-testid="repo-docker-access-section">{`${repo} ${dockerAccess ? "granted" : "not granted"}`}</div>
  ),
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const mockFetchHistory = vi.fn();
vi.mock("@/api/repoHistory", () => ({
  fetchHistory: (...a: unknown[]) => mockFetchHistory(...a),
  undoChange: vi.fn(),
}));

const mockFetchReadiness = vi.fn();
vi.mock("@/api/repoSetup", () => ({
  fetchReadiness: (...a: unknown[]) => mockFetchReadiness(...a),
  skipTesting: vi.fn(),
}));

const mockFetchJobs = vi.fn();
vi.mock("@/api/jobs", () => ({ fetchJobs: (...a: unknown[]) => mockFetchJobs(...a) }));

// What the Accounts and Test setup bodies draw from, for a page opened on one.
const mockFetchRepoCredentials = vi.fn();
const mockFetchCredentials = vi.fn();
vi.mock("@/api/credentials", () => ({
  fetchRepoCredentials: (...a: unknown[]) => mockFetchRepoCredentials(...a),
  fetchCredentials: () => mockFetchCredentials(),
}));
const mockFetchModelsCatalog = vi.fn();
vi.mock("@/api/provider", () => ({ fetchModelsCatalog: () => mockFetchModelsCatalog() }));
vi.mock("@/api/qaConfig", () => ({ fetchQaConfig: () => Promise.resolve(null) }));

import { RepoDetailPage } from "../RepoDetailPage";

const GOLD = /(^|\s)(bg-primary|bg-primary-dim|text-primary)(\s|$)/;

/** Every element drawn in the gold: a gold fill, a gold wash or gold text. */
function goldElements(root: HTMLElement): HTMLElement[] {
  return [...root.querySelectorAll<HTMLElement>("*")].filter((el) => GOLD.test(el.getAttribute("class") ?? ""));
}

const DETAIL = {
  repo: "acme/widgets",
  active: true,
  notes: "pilot repo",
  created_at: "2026-08-06T00:00:00Z",
  updated_at: "2026-08-06T00:00:00Z",
  forge: "github" as const,
  has_qa_config: true,
  docker_access: false,
  qa_config_updated_at: "2026-08-06T01:00:00Z",
  qa_config_updated_by: "user:alex",
  preferences: null,
  env_var_count: 2,
  env_secret_count: 1,
};

const READINESS = {
  repo: "acme/widgets",
  status: "steps_left",
  steps_left: 2,
  summary: "embry0 can read this code and open pull requests. It cannot test your app yet.",
  next_step: "testing",
  rows: [
    {
      key: "testing",
      required: true,
      state: "missing",
      title: "Test your app before showing you a change",
      says: "Testing is not set up yet. embry0 can still write code, but it cannot check that your app still works.",
      action: { kind: "start_setup", label: "Let embry0 set up testing" },
    },
  ],
  needs_docker: false,
  facts_seen_at: null,
};

const STANDARD = {
  repo: "acme/widgets",
  owner: "acme",
  kind: "design",
  mode: "enforce",
  last_audit_at: "2026-08-06T02:00:00Z",
  last_audit_job_id: "job-9",
  score: 88,
  findings_open: 2,
  exceptions: [],
  updated_at: null,
};

/** The Advanced settings sections, in page order, with the exact copy each one shows. */
const ADVANCED = [
  {
    key: "testSetup",
    title: "Test setup",
    chip: "Check first",
    what: "How embry0 starts your app on its own test computer and what it checks after each change.",
    takesEffect: "From the next test run, including jobs already running that have not reached their test step.",
    undo: "History keeps every version. Undo from History.",
  },
  {
    key: "appSettings",
    title: "App settings",
    chip: "Check first",
    what:
      "Settings and passwords handed to embry0's test computers for this repository. They never reach your live app, " +
      "but a live password here gives embry0's agents access to that live system.",
    takesEffect: "From the next job.",
    undo: "Removing a secret cannot be undone: embry0 does not keep secret values.",
  },
  {
    key: "workMachine",
    title: "Work machine and branches",
    chip: "Check first",
    what:
      "The prepared computer embry0 writes code on, the branch it starts from and asks to merge into, the commit author, " +
      "and the checks it runs before a pull request.",
    takesEffect: "From the next job.",
    undo: "History keeps every version. Undo from History.",
  },
  {
    key: "prReview",
    title: "Pull request review",
    chip: "Check first",
    what: "embry0 reads each ready pull request and leaves comments. It never approves or blocks a merge. Each review is a paid job.",
    takesEffect: "From the next pull request.",
    undo: "Turn it off again. History keeps every version.",
  },
  {
    key: "accounts",
    title: "Accounts",
    chip: "Admin only",
    what: "Which GitHub or GitLab account embry0 pushes as, and which AI account pays for its work.",
    takesEffect: "From the next job. A job already running keeps the account it started with.",
    undo: "Pick the earlier account again. Changes are recorded in the audit log.",
  },
  {
    key: "docker",
    title: "Docker access",
    chip: "Admin only",
    what:
      "Lets this repository's code start its own containers. Only needed for apps started with docker compose. " +
      "It also lets that code reach the passwords embry0 stores.",
    takesEffect: "From the next test computer.",
    undo: "An admin can turn it off, but that cannot take back anything already seen.",
  },
  {
    key: "designStandard",
    title: "Design checks",
    chip: "Admin only",
    what: "Whether embry0 checks this repository's screens against your company's design rules.",
    takesEffect: "From the next design check.",
    undo: "An admin sets it back on the organization's Standards page.",
  },
];

const section = (key: string) => screen.getByTestId(`advanced-${key}`) as HTMLDetailsElement;

function renderPage(path = "/repos/acme/widgets") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/repos/*" element={<RepoDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockFetchRepoDetail.mockReset();
  mockFetchReadiness.mockReset();
  mockFetchReadiness.mockResolvedValue(READINESS);
  mockFetchHistory.mockReset();
  mockFetchHistory.mockResolvedValue({ entries: [], before_history: false });
  mockUseRepoEnv.mockReturnValue({ data: [] });
  mockUseResolvedRepoEnv.mockReset().mockReturnValue({ data: undefined, isPending: false, isError: false, refetch: vi.fn() });
  mockUseQaRunsForRepo.mockReturnValue({ data: [] });
  mockFetchRepoCredentials.mockReset();
  mockFetchRepoCredentials.mockResolvedValue({ repo: "acme/widgets", bindings: [], effective: {} });
  mockFetchCredentials.mockReset();
  mockFetchCredentials.mockResolvedValue([]);
  mockFetchModelsCatalog.mockReset();
  mockFetchModelsCatalog.mockResolvedValue({ providers: [] });
});

/** A fetch that answers when the test says. */
function held() {
  let answer: (value: unknown) => void = () => {};
  const promise = new Promise((resolve) => (answer = resolve));
  return { promise, answer };
}

describe("RepoDetailPage (RAV-1253)", () => {
  it("renders registry state and puts every technical control under Advanced settings, in order", async () => {
    mockFetchRepoDetail.mockResolvedValue({ ...DETAIL, docker_access: true, standards: [STANDARD] });
    mockFetchJobs.mockResolvedValue({ jobs: [], total: 0, offset: 0, limit: 200 });
    renderPage();
    expect(await screen.findByRole("heading", { name: "acme/widgets" })).toBeInTheDocument();
    expect(screen.getByText("pilot repo")).toBeInTheDocument();
    const advanced = screen.getByRole("heading", { name: "Advanced settings" });
    expect(advanced).toHaveAttribute("id", "advanced");
    expect(
      screen.getByText("Exact controls for people who know the values. Most repositories never need them."),
    ).toBeInTheDocument();
    const sections = ADVANCED.map(({ key }) => section(key));
    expect(advanced.compareDocumentPosition(sections[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    for (let i = 1; i < sections.length; i++) {
      expect(sections[i - 1].compareDocumentPosition(sections[i]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    expect(within(section("testSetup")).getByTestId("qa-config-editor")).toHaveTextContent("acme/widgets");
    expect(within(section("workMachine")).getByTestId("repo-prefs-section")).toBeInTheDocument();
    expect(within(section("prReview")).getByTestId("pr-review-card")).toHaveTextContent("acme/widgets");
    const accounts = within(section("accounts")).getByTestId("repo-accounts-section");
    expect(accounts).toHaveTextContent("acme/widgets");
    expect(accounts).toHaveTextContent("github");
    expect(within(section("docker")).getByTestId("repo-docker-access-section")).toBeInTheDocument();
    expect(within(section("designStandard")).getByTestId("repo-standard-row")).toBeInTheDocument();
  });

  it("keeps every Advanced section closed on load", async () => {
    mockFetchRepoDetail.mockResolvedValue({ ...DETAIL, docker_access: true, standards: [STANDARD] });
    mockFetchJobs.mockResolvedValue({ jobs: [], total: 0, offset: 0, limit: 200 });
    renderPage();
    await screen.findByRole("heading", { name: "acme/widgets" });
    for (const { key } of ADVANCED) {
      expect(section(key).tagName).toBe("DETAILS");
      expect(section(key).open).toBe(false);
    }
  });

  it.each(ADVANCED)("says what $title does, its risk, when it takes effect and how to undo it", async (guide) => {
    mockFetchRepoDetail.mockResolvedValue({ ...DETAIL, docker_access: true, standards: [STANDARD] });
    mockFetchJobs.mockResolvedValue({ jobs: [], total: 0, offset: 0, limit: 200 });
    renderPage();
    await screen.findByRole("heading", { name: "acme/widgets" });
    const el = section(guide.key);
    expect(el).toHaveAttribute("id", `advanced-${guide.key}`);
    const summary = el.querySelector("summary") as HTMLElement;
    expect(within(summary).getByText(guide.title)).toBeInTheDocument();
    expect(within(summary).getByText(guide.chip)).toBeInTheDocument();
    expect(within(summary).getByText(guide.what)).toBeInTheDocument();
    expect(el).toHaveTextContent(`Takes effect: ${guide.takesEffect}`);
    expect(el).toHaveTextContent(`Undo: ${guide.undo}`);
    expect(goldElements(el)).toEqual([]);
  });

  it("opens the section a link points at", async () => {
    mockFetchRepoDetail.mockResolvedValue(DETAIL);
    renderPage("/repos/acme/widgets#advanced-testSetup");
    await screen.findByRole("heading", { name: "acme/widgets" });
    await waitFor(() => expect(section("testSetup").open).toBe(true));
    expect(section("appSettings").open).toBe(false);
    expect(section("accounts").open).toBe(false);
  });

  it("paints a page opened on a section once that section's body has its answers", async () => {
    mockFetchRepoDetail.mockResolvedValue(DETAIL);
    const bindings = held();
    const accounts = held();
    const catalog = held();
    mockFetchRepoCredentials.mockReturnValue(bindings.promise);
    mockFetchCredentials.mockReturnValue(accounts.promise);
    mockFetchModelsCatalog.mockReturnValue(catalog.promise);
    renderPage("/repos/acme/widgets#advanced-accounts");
    await waitFor(() => expect(mockFetchReadiness).toHaveBeenCalled());
    // Fetched beside the details, not once the page has drawn the body.
    expect(mockFetchRepoCredentials).toHaveBeenCalledWith("acme/widgets");
    await new Promise((r) => setTimeout(r, 20));
    bindings.answer({ repo: "acme/widgets", bindings: [], effective: {} });
    accounts.answer([]);
    await new Promise((r) => setTimeout(r, 20));
    // The body's height still waits on the catalog: no page under it yet.
    expect(screen.queryByRole("heading", { name: "acme/widgets" })).not.toBeInTheDocument();
    catalog.answer({ providers: [] });
    await screen.findByRole("heading", { name: "acme/widgets" });
    await waitFor(() => expect(section("accounts").open).toBe(true));
  });

  it("paints a page opened on a section whose body could not load", async () => {
    mockFetchRepoDetail.mockResolvedValue(DETAIL);
    mockFetchRepoCredentials.mockRejectedValue(new Error("boom"));
    renderPage("/repos/acme/widgets#advanced-accounts");
    expect(await screen.findByRole("heading", { name: "acme/widgets" })).toBeInTheDocument();
    await waitFor(() => expect(section("accounts").open).toBe(true));
  });

  it("waits on no section's body for an address that names none", async () => {
    mockFetchRepoDetail.mockResolvedValue(DETAIL);
    mockFetchModelsCatalog.mockReturnValue(held().promise);
    renderPage("/repos/acme/widgets#advanced");
    expect(await screen.findByRole("heading", { name: "acme/widgets" })).toBeInTheDocument();
    expect(mockFetchRepoCredentials).not.toHaveBeenCalled();
    expect(mockFetchModelsCatalog).not.toHaveBeenCalled();
  });

  it.each([
    [false, false, false],
    [true, false, true],
    [false, true, true],
  ])(
    "shows Docker access only when granted or needed (granted %s, setup needs it %s)",
    async (granted, needsDocker, shown) => {
      mockFetchRepoDetail.mockResolvedValue({ ...DETAIL, docker_access: granted });
      mockFetchReadiness.mockResolvedValue({ ...READINESS, needs_docker: needsDocker });
      renderPage();
      await screen.findByTestId("repo-readiness-chip");
      if (shown) {
        expect(section("docker")).toBeInTheDocument();
        expect(screen.getByTestId("repo-docker-access-section")).toHaveTextContent(
          `acme/widgets ${granted ? "granted" : "not granted"}`,
        );
      } else {
        expect(screen.queryByTestId("advanced-docker")).not.toBeInTheDocument();
        expect(screen.queryByTestId("repo-docker-access-section")).not.toBeInTheDocument();
      }
    },
  );

  it("lets the person add or change app settings in Environments", async () => {
    mockFetchRepoDetail.mockResolvedValue(DETAIL);
    renderPage();
    await screen.findByRole("heading", { name: "acme/widgets" });
    const link = within(section("appSettings")).getByTestId("repo-env-manage-link");
    expect(link).toHaveTextContent("Add or change values");
    expect(link).toHaveAttribute("href", "/environments?repo=acme%2Fwidgets");
    expect(screen.queryByText(/Manage in Environments/)).not.toBeInTheDocument();
  });

  it("opens on the setup checklist, under a header without registry badges", async () => {
    mockFetchRepoDetail.mockResolvedValue(DETAIL);
    renderPage();
    const heading = await screen.findByRole("heading", { name: "acme/widgets" });
    const checklist = await screen.findByTestId("setup-checklist");
    expect(heading.compareDocumentPosition(checklist) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const advanced = screen.getByRole("heading", { name: "Advanced settings" });
    expect(checklist.compareDocumentPosition(advanced) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(mockFetchReadiness).toHaveBeenCalledWith("acme/widgets");
    expect(screen.getByText("GitHub")).toBeInTheDocument();
    expect(screen.queryByText("Active")).not.toBeInTheDocument();
    expect(screen.queryByText("QA config stored")).not.toBeInTheDocument();
    expect(screen.queryByTestId("repo-config-meta")).not.toBeInTheDocument();
    expect(screen.queryByText(/user:alex/)).not.toBeInTheDocument();
  });

  it("links back to the repositories list", async () => {
    mockFetchRepoDetail.mockResolvedValue(DETAIL);
    renderPage();
    await screen.findByRole("heading", { name: "acme/widgets" });
    expect(screen.getByRole("link", { name: "← Repositories" })).toHaveAttribute("href", "/repos");
  });

  it.each([
    [{ status: "steps_left", steps_left: 2 }, "2 steps left"],
    [{ status: "steps_left", steps_left: 1 }, "1 step left"],
    [{ status: "ready", steps_left: 0, next_step: null }, "Ready"],
    [{ status: "needs_admin", steps_left: 3, next_step: "code_access" }, "Needs an admin"],
    [{ status: "setup_to_review", steps_left: 2 }, "Setup to review"],
  ])("shows one status chip from the setup status (%o)", async (overrides, chip) => {
    mockFetchRepoDetail.mockResolvedValue(DETAIL);
    mockFetchReadiness.mockResolvedValue({ ...READINESS, ...overrides });
    renderPage();
    expect(await screen.findByTestId("repo-readiness-chip")).toHaveTextContent(chip);
  });

  it("says a hidden repository is still worked on", async () => {
    mockFetchRepoDetail.mockResolvedValue({ ...DETAIL, active: false });
    renderPage();
    await screen.findByRole("heading", { name: "acme/widgets" });
    expect(screen.getByText("Hidden from lists. embry0 still works on this repository.")).toBeInTheDocument();
    expect(screen.queryByText("Inactive")).not.toBeInTheDocument();
  });

  it("leaves the hidden note out for an active repository", async () => {
    mockFetchRepoDetail.mockResolvedValue(DETAIL);
    renderPage();
    await screen.findByRole("heading", { name: "acme/widgets" });
    expect(screen.queryByText(/Hidden from lists/)).not.toBeInTheDocument();
  });

  it("paints the page once the setup status is known, so nothing moves under the reader", async () => {
    mockFetchRepoDetail.mockResolvedValue(DETAIL);
    let answer: (value: unknown) => void = () => {};
    mockFetchReadiness.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    renderPage();
    await waitFor(() => expect(mockFetchRepoDetail).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByRole("heading", { name: "acme/widgets" })).not.toBeInTheDocument();
    answer(READINESS);
    await screen.findByRole("heading", { name: "acme/widgets" });
    expect(screen.getByTestId("repo-readiness-chip")).toBeInTheDocument();
  });

  it("still renders the page when the setup status fails to load", async () => {
    mockFetchRepoDetail.mockResolvedValue(DETAIL);
    mockFetchReadiness.mockRejectedValue(new Error("boom"));
    renderPage();
    const checklist = await screen.findByTestId("setup-checklist");
    expect(await within(checklist).findByText("Could not load the setup status.")).toBeInTheDocument();
    expect(screen.queryByTestId("repo-readiness-chip")).not.toBeInTheDocument();
    expect(screen.getByTestId("qa-config-editor")).toHaveTextContent("acme/widgets");
    expect(screen.getByTestId("repo-accounts-section")).toBeInTheDocument();
  });

  it("puts History after the settings and before the recent test runs", async () => {
    mockFetchRepoDetail.mockResolvedValue({ ...DETAIL, docker_access: true });
    renderPage();
    const history = await screen.findByTestId("history-panel");
    const docker = section("docker");
    const runs = screen.getByRole("heading", { name: "Recent test runs" });
    expect(docker.compareDocumentPosition(history) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(history.compareDocumentPosition(runs) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(mockFetchHistory).toHaveBeenCalledWith("acme/widgets");
    expect(screen.queryByRole("heading", { name: "Recent QA runs" })).not.toBeInTheDocument();
    expect(screen.getByText("No test runs yet.")).toBeInTheDocument();
  });

  it("masks secret env values and links runs", async () => {
    mockFetchRepoDetail.mockResolvedValue(DETAIL);
    mockUseRepoEnv.mockReturnValue({
      data: [
        { key: "API_URL", value: "http://x", var_type: "config", scope: "app" },
        { key: "TOKEN", value: "should-not-render", var_type: "secret", scope: "app" },
      ],
    });
    mockUseQaRunsForRepo.mockReturnValue({
      data: [{ job_id: "job-abc", repo: "acme/widgets", started_at: "t", overall_status: "passed", app_count: 1 }],
    });
    renderPage();
    await screen.findByRole("heading", { name: "acme/widgets" });
    const rows = screen.getAllByTestId("repo-env-row");
    expect(rows).toHaveLength(2);
    expect(screen.getByText("****")).toBeInTheDocument();
    expect(screen.queryByText("should-not-render")).not.toBeInTheDocument();
    expect(screen.getByTestId("repo-run-row")).toHaveAttribute("href", "/qa/runs/job-abc");
  });

  it("shows the repository's standards row with a link to the audit job, under Design checks", async () => {
    mockFetchJobs.mockResolvedValue({ jobs: [], total: 0, offset: 0, limit: 200 });
    mockFetchRepoDetail.mockResolvedValue({ ...DETAIL, standards: [STANDARD] });
    renderPage();
    const row = await screen.findByTestId("repo-standard-row");
    expect(row).toHaveTextContent("Enforce");
    expect(screen.getByTestId("repo-standard-score")).toHaveTextContent("88");
    expect(screen.getByTestId("repo-standard-findings")).toHaveTextContent("2");
    expect(screen.getByTestId("repo-standard-job")).toHaveAttribute("href", "/jobs/job-9");
    expect(section("designStandard")).toContainElement(row);
  });

  it("leaves the standards row and its section out for a server without standards", async () => {
    mockFetchRepoDetail.mockResolvedValue(DETAIL);
    renderPage();
    await screen.findByRole("heading", { name: "acme/widgets" });
    expect(screen.queryByTestId("repo-standards")).toBeNull();
    expect(screen.queryByTestId("advanced-designStandard")).toBeNull();
  });

  it("shows a clear error for unregistered repos", async () => {
    mockFetchRepoDetail.mockRejectedValue(Object.assign(new Error("404"), { response: { status: 404 } }));
    renderPage();
    expect(await screen.findByText(/not registered in embry0/)).toBeInTheDocument();
  });

  it("renders a GitLab nested subgroup path (#162)", async () => {
    mockFetchRepoDetail.mockResolvedValue({ ...DETAIL, repo: "acme/products/fbr" });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter initialEntries={["/repos/acme/products/fbr"]}>
          <Routes>
            <Route path="/repos/*" element={<RepoDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("heading", { name: "acme/products/fbr" })).toBeInTheDocument();
    expect(screen.getByTestId("repo-accounts-section")).toHaveTextContent("acme/products/fbr");
    expect(mockFetchRepoDetail).toHaveBeenCalledWith("acme/products/fbr");
  });

  it("shows the repo's Docker access grant (#522)", async () => {
    mockFetchRepoDetail.mockResolvedValue({ ...DETAIL, docker_access: true });
    renderPage();
    await screen.findByRole("heading", { name: "acme/widgets" });
    expect(within(section("docker")).getByText("Docker access")).toBeInTheDocument();
    expect(screen.getByTestId("repo-docker-access-section")).toHaveTextContent("acme/widgets granted");
  });

  it("treats a missing docker_access field as not granted", async () => {
    const { docker_access: _unused, ...withoutFlag } = DETAIL;
    void _unused;
    mockFetchRepoDetail.mockResolvedValue(withoutFlag);
    mockFetchReadiness.mockResolvedValue({ ...READINESS, needs_docker: true });
    renderPage();
    await screen.findByTestId("repo-readiness-chip");
    expect(screen.getByTestId("repo-docker-access-section")).toHaveTextContent("not granted");
  });

  it("shows the effective environment on request, each value marked Repo or Global", async () => {
    mockFetchRepoDetail.mockResolvedValue(DETAIL);
    mockUseRepoEnv.mockReturnValue({ data: [{ key: "API_URL", value: "https://repo.example.test", var_type: "config", description: "" }] });
    mockUseResolvedRepoEnv.mockReturnValue({
      data: [
        { key: "API_URL", value: "https://repo.example.test", var_type: "config", description: "" },
        { key: "SHARED_TOKEN", value: "", var_type: "secret", description: "", masked_preview: "SHAR••••7Q2F" },
      ],
      isPending: false,
      isError: false,
      refetch: vi.fn(),
    });
    renderPage();
    await screen.findByRole("heading", { level: 1 });
    expect(mockUseResolvedRepoEnv).toHaveBeenLastCalledWith("acme/widgets", false);
    const toggle = screen.getByRole("switch", { name: "Effective for this repo" });
    await userEvent.click(toggle);
    expect(mockUseResolvedRepoEnv).toHaveBeenLastCalledWith("acme/widgets", true);
    const rows = screen.getAllByTestId("repo-env-row");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("Repo")).toBeInTheDocument();
    expect(within(rows[1]).getByText("Global")).toBeInTheDocument();
    expect(within(rows[1]).getByText("SHAR••••7Q2F")).toBeInTheDocument();
  });

  it("marks every effective variable Global when the repo sets none", async () => {
    mockFetchRepoDetail.mockResolvedValue(DETAIL);
    mockUseRepoEnv.mockReturnValue({ data: [] });
    mockUseResolvedRepoEnv.mockReturnValue({
      data: [{ key: "SHARED", value: "x", var_type: "config", description: "" }],
      isPending: false,
      isError: false,
      refetch: vi.fn(),
    });
    renderPage();
    await screen.findByRole("heading", { level: 1 });
    expect(screen.getByText("No repo-scoped environment variables.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("switch", { name: "Effective for this repo" }));
    expect(screen.queryByText("No repo-scoped environment variables.")).toBeNull();
    expect(within(screen.getByTestId("repo-env-row")).getByText("Global")).toBeInTheDocument();
  });
});
