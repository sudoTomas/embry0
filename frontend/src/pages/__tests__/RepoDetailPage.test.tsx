import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockFetchRepoDetail = vi.fn();
vi.mock("@/api/repos", () => ({
  fetchRepoDetail: (...a: unknown[]) => mockFetchRepoDetail(...a),
}));

const mockUseRepoEnv = vi.fn();
vi.mock("@/hooks/useEnvironments", () => ({
  useRepoEnv: () => mockUseRepoEnv(),
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

import { RepoDetailPage } from "../RepoDetailPage";

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

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/repos/acme/widgets"]}>
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
  mockUseQaRunsForRepo.mockReturnValue({ data: [] });
});

describe("RepoDetailPage (RAV-1253)", () => {
  it("renders registry state and its sections", async () => {
    mockFetchRepoDetail.mockResolvedValue(DETAIL);
    renderPage();
    expect(await screen.findByRole("heading", { name: "acme/widgets" })).toBeInTheDocument();
    expect(screen.getByText("pilot repo")).toBeInTheDocument();
    expect(screen.getByTestId("qa-config-editor")).toHaveTextContent("acme/widgets");
    expect(screen.getByTestId("repo-accounts-section")).toHaveTextContent("acme/widgets");
    expect(screen.getByTestId("repo-accounts-section")).toHaveTextContent("github");
    const environment = screen.getByRole("heading", { name: "Environment" });
    const accounts = screen.getByRole("heading", { name: "Accounts" });
    const sandbox = screen.getByRole("heading", { name: "Sandbox preferences" });
    expect(environment.compareDocumentPosition(accounts) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(accounts.compareDocumentPosition(sandbox) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByTestId("repo-prefs-section")).toBeInTheDocument();
    const review = screen.getByRole("heading", { name: "Pull request review" });
    expect(sandbox.compareDocumentPosition(review) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByTestId("pr-review-card")).toHaveTextContent("acme/widgets");
    expect(screen.getByTestId("repo-env-manage-link")).toHaveAttribute(
      "href",
      "/environments?repo=acme%2Fwidgets",
    );
  });

  it("opens on the setup checklist, under a header without registry badges", async () => {
    mockFetchRepoDetail.mockResolvedValue(DETAIL);
    renderPage();
    const heading = await screen.findByRole("heading", { name: "acme/widgets" });
    const checklist = await screen.findByTestId("setup-checklist");
    expect(heading.compareDocumentPosition(checklist) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    const qa = screen.getByRole("heading", { name: "QA config" });
    expect(checklist.compareDocumentPosition(qa) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
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
    mockFetchRepoDetail.mockResolvedValue(DETAIL);
    renderPage();
    const history = await screen.findByTestId("history-panel");
    const docker = screen.getByRole("heading", { name: "Docker access" });
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

  it("shows the repository's standards row with a link to the audit job, above the QA config", async () => {
    mockFetchJobs.mockResolvedValue({ jobs: [], total: 0, offset: 0, limit: 200 });
    mockFetchRepoDetail.mockResolvedValue({
      ...DETAIL,
      standards: [
        {
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
        },
      ],
    });
    renderPage();
    const row = await screen.findByTestId("repo-standard-row");
    expect(row).toHaveTextContent("Enforce");
    expect(screen.getByTestId("repo-standard-score")).toHaveTextContent("88");
    expect(screen.getByTestId("repo-standard-findings")).toHaveTextContent("2");
    expect(screen.getByTestId("repo-standard-job")).toHaveAttribute("href", "/jobs/job-9");
    const qa = screen.getByRole("heading", { name: "QA config" });
    expect(row.compareDocumentPosition(qa) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("leaves the standards row out for a server without standards", async () => {
    mockFetchRepoDetail.mockResolvedValue(DETAIL);
    renderPage();
    await screen.findByRole("heading", { name: "acme/widgets" });
    expect(screen.queryByTestId("repo-standards")).toBeNull();
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
    expect(screen.getByRole("heading", { name: "Docker access" })).toBeInTheDocument();
    expect(screen.getByTestId("repo-docker-access-section")).toHaveTextContent("acme/widgets granted");
  });

  it("treats a missing docker_access field as not granted", async () => {
    const { docker_access: _unused, ...withoutFlag } = DETAIL;
    void _unused;
    mockFetchRepoDetail.mockResolvedValue(withoutFlag);
    renderPage();
    await screen.findByRole("heading", { name: "acme/widgets" });
    expect(screen.getByTestId("repo-docker-access-section")).toHaveTextContent("not granted");
  });
});
