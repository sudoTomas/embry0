import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
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

const mockFetchJob = vi.fn();
vi.mock("@/api/jobs", () => ({ fetchJob: (...a: unknown[]) => mockFetchJob(...a) }));

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
  mockUseRepoEnv.mockReturnValue({ data: [] });
  mockUseQaRunsForRepo.mockReturnValue({ data: [] });
});

describe("RepoDetailPage (RAV-1253)", () => {
  it("renders registry state and its sections", async () => {
    mockFetchRepoDetail.mockResolvedValue(DETAIL);
    renderPage();
    expect(await screen.findByRole("heading", { name: "acme/widgets" })).toBeInTheDocument();
    expect(screen.getByText("QA config stored")).toBeInTheDocument();
    expect(screen.getByText("pilot repo")).toBeInTheDocument();
    expect(screen.getByTestId("repo-config-meta")).toHaveTextContent("user:alex");
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
    mockFetchJob.mockResolvedValue({ job_id: "job-9", status: "completed" });
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
    mockFetchRepoDetail.mockRejectedValue(new Error("404"));
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
