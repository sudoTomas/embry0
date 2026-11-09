import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockFetchAuditActions = vi.fn();
vi.mock("@/api/audit", () => ({
  fetchAuditActions: (...a: unknown[]) => mockFetchAuditActions(...a),
}));

import { IssueActivityFeed } from "../IssueActivityFeed";

const REGISTRY = [
  { action: "issue.created", label: "Created", replaced_by: null },
  { action: "issue.input_created", label: "Question asked", replaced_by: null },
];

function entry(id: number, action: string) {
  return { id, action, actor: "op@example.com", details: {}, issue_id: "iss-1", created_at: "2026-08-01T12:00:00Z" };
}

beforeEach(() => {
  mockFetchAuditActions.mockReset();
  mockFetchAuditActions.mockResolvedValue(REGISTRY);
});

describe("IssueActivityFeed", () => {
  it("reads each event in the registry's words, and one it does not list in sentence case, never raw", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={qc}>
        <IssueActivityFeed
          entries={[entry(1, "issue.created"), entry(2, "issue.input_created"), entry(3, "issue.pipeline_paused")]}
        />
      </QueryClientProvider>,
    );
    expect(await screen.findByText("Question asked")).toBeInTheDocument();
    expect(screen.getByText("Created")).toBeInTheDocument();
    expect(screen.getByText("Issue pipeline paused")).toBeInTheDocument();
    expect(screen.queryByText(/issue\.\w+/)).toBeNull();
  });
});
