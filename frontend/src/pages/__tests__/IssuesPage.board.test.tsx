// The Issues Board counts every issue, not the 20 on the list's current page:
// 55 issues of which 51 are closed must read Closed 51, not the 18 closed
// issues that happen to sit on page one.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { IssueFilters, IssueResponse } from "@/lib/types";
import { useIssuesStore } from "@/stores/issuesStore";

const NOW = "2026-10-18T00:00:00Z";
const issue = (id: string, status: string): IssueResponse =>
  ({
    id, title: `Issue ${id}`, body: "", status, priority: "medium", labels: [], repo: "acme/backend",
    parent_issue_id: null, tracker: null, tracker_key: null, tracker_url: null, github_sync_enabled: false,
    github_synced_at: null, created_by: "user", created_at: NOW, updated_at: NOW, children_count: 0,
    children_closed_count: 0, jobs_count: 0, active_agent: null, notification_channels: [],
  }) as unknown as IssueResponse;

// What the API holds: 1 open, 51 closed, 3 cancelled.
const ALL = [
  issue("o1", "open"),
  ...Array.from({ length: 51 }, (_, i) => issue(`c${i}`, "closed")),
  ...Array.from({ length: 3 }, (_, i) => issue(`x${i}`, "cancelled")),
];
const fetchIssues = vi.fn(async (f: IssueFilters = {}) => {
  const matching = ALL.filter((i) => !f.status || i.status === f.status);
  const page = matching.slice(f.offset ?? 0, (f.offset ?? 0) + (f.limit ?? 50));
  return { issues: page, total: matching.length, offset: f.offset ?? 0, limit: f.limit ?? 50 };
});
vi.mock("@/api/issues", () => ({ fetchIssues: (f: IssueFilters) => fetchIssues(f), updateIssue: vi.fn() }));
vi.mock("@/api/cases", () => ({
  fetchProposals: vi.fn(async () => ({ cases: [], total: 0, limit: 50, next_cursor: null })),
  acceptCase: vi.fn(),
  rejectCase: vi.fn(),
}));

import { IssuesPage } from "../IssuesPage";

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <IssuesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  fetchIssues.mockClear();
  useIssuesStore.setState({ viewMode: "board" });
});

describe("Issues board counts", () => {
  it("counts each column over every issue, not the list page", async () => {
    renderPage();
    const closed = await screen.findByTestId("board-column-closed");
    expect(await within(closed).findByTestId("board-column-count")).toHaveTextContent(/^51$/);
    expect(within(screen.getByTestId("board-column-open")).getByTestId("board-column-count")).toHaveTextContent(/^1$/);
    expect(within(screen.getByTestId("board-column-cancelled")).getByTestId("board-column-count")).toHaveTextContent(/^3$/);
  });
});
