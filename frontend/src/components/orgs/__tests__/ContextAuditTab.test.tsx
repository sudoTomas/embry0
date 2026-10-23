import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";

const mockFetch = vi.fn();
vi.mock("@/api/orgs", async () => {
  const actual = await vi.importActual<typeof import("@/api/orgs")>("@/api/orgs");
  return { ...actual, fetchContextAudit: (...a: unknown[]) => mockFetch(...a) };
});

import type { ContextAuditJob, ContextAuditRepo, ContextAuditSurvey } from "@/api/orgs";
import { ContextAuditTab, ContextAuditTable } from "../ContextAuditTab";

function job(overrides: Partial<ContextAuditJob> = {}): ContextAuditJob {
  return {
    job_id: "job-1",
    status: "completed",
    created_at: "2026-10-20T12:00:00Z",
    finished_at: "2026-10-20T12:09:00Z",
    total_cost_usd: 1.5,
    error_code: null,
    stale: 3,
    files_read: 2,
    ...overrides,
  };
}

function row(repo: string, overrides: Partial<ContextAuditRepo> = {}): ContextAuditRepo {
  return {
    repo,
    files: 2,
    fingerprint: "f".repeat(64),
    head_sha: "1".repeat(40),
    newest_job: job(),
    refused_at: null,
    due: false,
    reason: "current",
    ...overrides,
  };
}

function survey(repos: ContextAuditRepo[], overrides: Partial<ContextAuditSurvey> = {}): ContextAuditSurvey {
  return {
    owner: "acme",
    context_audit_days: 7,
    read_at: "2026-10-20T12:00:00Z",
    template: "context-audit",
    repos,
    unread: [],
    ...overrides,
  };
}

const SURVEY = survey([
  row("acme/few", { newest_job: job({ job_id: "job-few", stale: 1 }) }),
  row("acme/new", { newest_job: null, due: true, reason: "first" }),
  row("acme/many", { newest_job: job({ job_id: "job-many", stale: 16, files_read: 6, total_cost_usd: 2.66 }) }),
  row("acme/cut", { newest_job: job({ job_id: "job-cut", stale: null, files_read: null }) }),
  row("acme/turns", {
    newest_job: job({ job_id: "job-turns", status: "failed", error_code: "ERR_AGENT_MAX_TURNS", stale: null, total_cost_usd: 2.98 }),
  }),
  row("acme/live", { newest_job: job({ job_id: "job-live", status: "running", finished_at: null, stale: null }), reason: "live" }),
]);

function client() {
  return new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
}

function renderTable(s: ContextAuditSurvey = SURVEY) {
  return render(
    <MemoryRouter>
      <ContextAuditTable survey={s} />
    </MemoryRouter>,
  );
}

const rows = () => screen.getAllByTestId("context-audit-row");
const repoOf = (r: HTMLElement) => within(r).getByTestId("context-audit-repo").textContent;

describe("ContextAuditTable", () => {
  it("puts the repositories with the most outdated claims first, unknown counts last", () => {
    renderTable();
    expect(rows().map(repoOf)).toEqual(["acme/many", "acme/few", "acme/cut", "acme/live", "acme/new", "acme/turns"]);
    expect(rows().map((r) => within(r).getByTestId("context-audit-stale").textContent)).toEqual([
      "16",
      "1",
      "?",
      "-",
      "-",
      "-",
    ]);
  });

  it("links each audit to its job page, where the findings are, and says why one failed", () => {
    renderTable();
    const many = rows()[0];
    expect(within(many).getByTestId("context-audit-job")).toHaveAttribute("href", "/jobs/job-many");
    expect(within(many).getByTestId("context-audit-repo")).toHaveAttribute("href", "/repos/acme/many");
    const turns = rows().find((r) => repoOf(r) === "acme/turns")!;
    expect(within(turns).getByTestId("context-audit-failure")).toHaveTextContent("stopped at the turn limit");
    const never = rows().find((r) => repoOf(r) === "acme/new")!;
    expect(within(never).queryByTestId("context-audit-job")).toBeNull();
    expect(never).toHaveTextContent("Never");
    expect(within(never).getByTestId("context-audit-next")).toHaveTextContent("Due: never audited");
  });

  it("totals the audits: completed ones counted, unknown counts not taken for zero", () => {
    renderTable();
    expect(screen.getByTestId("context-audit-totals")).toHaveTextContent(
      "6 repositories with instruction files · 3 audited · 17 outdated claims · 1 failed · $10.14 on the latest audits",
    );
  });

  it("shows a start refused before any job, and the repositories the forge did not answer for", () => {
    renderTable(
      survey([row("acme/web", { newest_job: null, refused_at: "2026-10-20T12:00:00Z" })], {
        unread: [{ repo: "acme/gone", detail: "reading the newest commit answered 404" }],
      }),
    );
    expect(rows()[0]).toHaveTextContent("Start refused");
    expect(screen.getByTestId("context-audit-unread")).toHaveTextContent(
      "acme/gone was not read: reading the newest commit answered 404",
    );
  });

  it("says when no registered repository has instruction files", () => {
    renderTable(survey([]));
    expect(screen.getByText("No instruction files found")).toBeInTheDocument();
    expect(screen.queryByTestId("context-audit-table")).toBeNull();
  });
});

describe("ContextAuditTab", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  function renderTab(days: number | null = 7) {
    return render(
      <QueryClientProvider client={client()}>
        <MemoryRouter>
          <ContextAuditTab owner="acme" days={days} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  }

  it("serves the last reading, and reads the forge again only when asked", async () => {
    mockFetch.mockResolvedValueOnce(survey([row("acme/web", { newest_job: job({ stale: 2 }) })]));
    renderTab();
    expect(await screen.findByTestId("context-audit-table")).toBeInTheDocument();
    expect(mockFetch).toHaveBeenCalledWith("acme");
    expect(screen.getByTestId("context-audit-read-at")).toBeInTheDocument();

    mockFetch.mockResolvedValueOnce(survey([row("acme/web", { newest_job: job({ stale: 5 }) })]));
    fireEvent.click(screen.getByTestId("context-audit-refresh"));
    await waitFor(() => expect(mockFetch).toHaveBeenLastCalledWith("acme", true));
    await waitFor(() => expect(screen.getByTestId("context-audit-stale")).toHaveTextContent("5"));
  });

  it("says when the schedule is off", async () => {
    mockFetch.mockResolvedValueOnce(survey([], { context_audit_days: null }));
    renderTab(null);
    expect(screen.getByTestId("context-audit-schedule")).toHaveTextContent("The schedule is off");
    expect(await screen.findByText("No instruction files found")).toBeInTheDocument();
  });

  it("names a server without the schedule rather than a generic failure", async () => {
    mockFetch.mockRejectedValueOnce({
      response: { status: 503, data: { detail: "the context audit schedule is not initialized" } },
    });
    renderTab();
    expect(await screen.findByText("The context audit is not running on this server.")).toBeInTheDocument();
  });
});
