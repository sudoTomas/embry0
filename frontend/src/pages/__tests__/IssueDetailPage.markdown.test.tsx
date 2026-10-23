// An issue's description is Markdown (watcher tickets carry "## Evidence"
// sections, GitHub escapes brackets): it renders as Markdown, and never as
// raw HTML.
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const NOW = new Date().toISOString();
const ISSUE = {
  id: "iss-1",
  title: "Fix the login redirect",
  body: [
    "## Evidence",
    "",
    "- the login redirect drops the \\[next\\] parameter",
    "- see `auth.ts`",
    "",
    "| Route | Status |",
    "|---|---|",
    "| /login | 302 |",
    "",
    '<img src="x" onerror="alert(1)"><script>alert(2)</script>',
    "",
    "[the run](javascript:alert(3))",
  ].join("\n"),
  status: "open",
  priority: "medium",
  labels: [],
  repo: "acme/backend",
  parent_issue_id: null,
  tracker: null,
  tracker_key: null,
  tracker_url: null,
  github_sync_enabled: false,
  github_synced_at: null,
  created_by: "user",
  created_at: NOW,
  updated_at: NOW,
  children: [],
  children_count: 0,
  children_closed_count: 0,
  jobs: [],
  jobs_count: 0,
  active_agent: null,
  notification_channels: ["dashboard"],
};

vi.mock("@/hooks/useIssues", () => ({
  useIssue: () => ({ data: ISSUE, isLoading: false, isError: false, refetch: vi.fn() }),
  useIssueActivity: () => ({ data: [] }),
  useTriageIssue: () => ({ mutate: vi.fn(), isPending: false }),
  useSyncIssue: () => ({ mutate: vi.fn(), isPending: false }),
  useDeleteIssue: () => ({ mutate: vi.fn(), isPending: false }),
  useUpdateIssue: () => ({ mutate: vi.fn() }),
}));
vi.mock("@/components/issues/IssueActivityFeed", () => ({ IssueActivityFeed: () => null }));
vi.mock("@/components/issues/IssueQuestionsPanel", () => ({ IssueQuestionsPanel: () => null }));

import { IssueDetailPage } from "../IssueDetailPage";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/issues/iss-1"]}>
        <Routes>
          <Route path="/issues/:id" element={<IssueDetailPage />} />
          <Route path="/issues" element={<p>issue list</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("an issue description", () => {
  it("renders headings, lists, code and tables from Markdown", () => {
    renderPage();
    expect(screen.getByRole("heading", { name: "Evidence" })).toBeInTheDocument();
    expect(screen.getByText(/drops the \[next\] parameter/)).toBeInTheDocument();
    expect(screen.queryByText(/## Evidence/)).toBeNull();
    expect(screen.getByText("auth.ts").tagName).toBe("CODE");
    expect(screen.getByRole("cell", { name: "/login" })).toBeInTheDocument();
  });

  it("renders no raw HTML and no script links", () => {
    const { container } = renderPage();
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector('a[href^="javascript"]')).toBeNull();
  });
});
