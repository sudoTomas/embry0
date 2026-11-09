import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockFetchAuditLog = vi.fn();
const mockFetchAuditActions = vi.fn();
vi.mock("@/api/audit", () => ({
  fetchAuditLog: (...a: unknown[]) => mockFetchAuditLog(...a),
  fetchAuditActions: (...a: unknown[]) => mockFetchAuditActions(...a),
}));

import { AuditPage } from "../AuditPage";

const ENTRY = {
  id: 1,
  action: "user.login",
  actor: "t@example.com",
  details: { tier: "admin" },
  issue_id: null,
  created_at: "2026-08-01T12:00:00Z",
};

const ISSUE_ENTRY = {
  id: 2,
  action: "issue.created",
  actor: "op@example.com",
  details: {},
  issue_id: "iss-abc123def456",
  created_at: "2026-08-01T13:00:00Z",
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <AuditPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

/**
 * The audit table once rows arrive (the loading skeleton is a table too).
 * The first lookup in the file pays the cold render, which exceeds the
 * default 1 s wait when several suites share the machine (#106).
 */
async function auditTable() {
  const head = await screen.findByRole("columnheader", { name: "Event" }, { timeout: 4000 });
  return within(head.closest("table") as HTMLElement);
}

/** A registry as GET /audit/actions serves one: the current actions in its order, then a retired name. Two of them
 * no dashboard map ever held, and one reads otherwise than its name in sentence case. */
const REGISTRY = [
  { action: "user.login", label: "Signed in", replaced_by: null },
  { action: "apikey.created", label: "API key created", replaced_by: null },
  { action: "apikey.repo_refused", label: "API key refused a repository", replaced_by: null },
  { action: "issue.created", label: "Created", replaced_by: null },
  { action: "widget.polished", label: "A widget was polished", replaced_by: null },
  { action: "job.review.dismissed", label: "Job dismissed", replaced_by: null },
  { action: "quest.dismissed", label: "Job dismissed", replaced_by: "job.review.dismissed" },
];

beforeEach(() => {
  mockFetchAuditLog.mockReset();
  mockFetchAuditActions.mockReset();
  mockFetchAuditActions.mockResolvedValue(REGISTRY);
});

describe("AuditPage", () => {
  it("renders labeled events with actor and detail", async () => {
    mockFetchAuditLog.mockResolvedValue({ entries: [ENTRY], total: 1 });
    renderPage();
    const table = await auditTable();
    expect(table.getByText("Signed in")).toBeInTheDocument();
    expect(table.getByText("t@example.com")).toBeInTheDocument();
    expect(table.getByText("tier admin")).toBeInTheDocument();
  });

  it("reads an action the registry does not list in sentence case, never raw", async () => {
    mockFetchAuditLog.mockResolvedValue({
      entries: [
        { ...ENTRY, id: 3, action: "watcher_stack_updated", details: {} },
        { ...ENTRY, id: 4, action: "organization.need_reopen", details: {} },
      ],
      total: 2,
    });
    renderPage();
    const table = await auditTable();
    expect(table.getByText("Watcher stack updated")).toBeInTheDocument();
    expect(table.getByText("Organization need reopen")).toBeInTheDocument();
    expect(table.queryByText(/watcher_stack_updated|organization\.need_reopen/)).toBeNull();
  });

  it("names the action filter's options in words", async () => {
    mockFetchAuditLog.mockResolvedValue({ entries: [ENTRY], total: 1 });
    renderPage();
    const select = within(await screen.findByLabelText("Actions"));
    expect(select.getByRole("option", { name: "All actions" })).toBeInTheDocument();
    expect(select.getByRole("option", { name: "API key created" })).toHaveValue("apikey.created");
  });

  it("offers every current action the registry serves, in its order, and a retired name not as its own", async () => {
    mockFetchAuditLog.mockResolvedValue({ entries: [ENTRY], total: 1 });
    renderPage();
    await auditTable();
    const offered = within(screen.getByLabelText("Actions"))
      .getAllByRole("option")
      .slice(1)
      .map((option) => [(option as HTMLOptionElement).value, option.textContent]);
    const current = REGISTRY.filter((entry) => !entry.replaced_by).map((entry) => [entry.action, entry.label]);
    expect(offered, "the Actions filter lists what GET /audit/actions serves").toEqual(current);
    expect(mockFetchAuditActions).toHaveBeenCalled();
  });

  it("reads each row in the registry's words, a retired name as the action that replaced it", async () => {
    mockFetchAuditLog.mockResolvedValue({
      entries: [
        { ...ENTRY, id: 5, action: "apikey.repo_refused", details: {} },
        { ...ENTRY, id: 6, action: "quest.dismissed", details: {} },
        { ...ENTRY, id: 7, action: "widget.polished", details: {} },
      ],
      total: 3,
    });
    renderPage();
    const table = await auditTable();
    const events = table
      .getAllByRole("row")
      .slice(1)
      .map((row) => within(row).getAllByRole("cell")[0].textContent);
    expect(events, "each row reads in the words GET /audit/actions serves").toEqual([
      "API key refused a repository",
      "Job dismissed",
      "A widget was polished",
    ]);
  });

  it("keeps showing the rows when the registry cannot be read, says so, and reads it again on request", async () => {
    mockFetchAuditLog.mockResolvedValue({
      entries: [
        { ...ENTRY, details: {} },
        { ...ENTRY, id: 5, action: "apikey.repo_refused", details: {} },
      ],
      total: 2,
    });
    mockFetchAuditActions.mockRejectedValueOnce(new Error("boom"));
    renderPage();
    const notice = await screen.findByRole("status", {}, { timeout: 4000 });
    expect(notice).toHaveTextContent("Could not load the action names");
    expect(screen.queryByText("Could not load the audit log"), "the log itself was read").toBeNull();
    const table = await auditTable();
    const events = () =>
      table
        .getAllByRole("row")
        .slice(1)
        .map((row) => within(row).getAllByRole("cell")[0].textContent);
    expect(events(), "the rows stay, each named from its identifier").toEqual(["User login", "Apikey repo refused"]);
    expect(within(screen.getByLabelText("Actions")).getAllByRole("option"), "the filter lists no actions").toHaveLength(1);

    await userEvent.setup().click(within(notice).getByRole("button", { name: "Try again" }));
    expect(await table.findByText("API key refused a repository")).toBeInTheDocument();
    expect(screen.queryByRole("status"), "the notice goes once the names are read").toBeNull();
    expect(mockFetchAuditActions).toHaveBeenCalledTimes(2);
  });

  it("links issue-scoped rows to the issue page", async () => {
    mockFetchAuditLog.mockResolvedValue({ entries: [ISSUE_ENTRY], total: 1 });
    renderPage();
    const link = await screen.findByRole("link", { name: /iss-abc123de/ });
    expect(link).toHaveAttribute("href", "/issues/iss-abc123def456");
  });

  it("shows the empty state when no rows match", async () => {
    mockFetchAuditLog.mockResolvedValue({ entries: [], total: 0 });
    renderPage();
    expect(await screen.findByText(/no audit events/i)).toBeInTheDocument();
  });

  it("passes the action filter and resets paging", async () => {
    mockFetchAuditLog.mockResolvedValue({ entries: [ENTRY], total: 1 });
    renderPage();
    await auditTable();
    const user = userEvent.setup();
    await user.selectOptions(screen.getByLabelText("Actions"), "apikey.created");
    await vi.waitFor(() => {
      const lastCall = mockFetchAuditLog.mock.calls.at(-1)?.[0] as Record<string, unknown>;
      expect(lastCall.action).toBe("apikey.created");
      expect(lastCall.offset).toBe(0);
    });
  });

  it("debounces the actor filter", async () => {
    mockFetchAuditLog.mockResolvedValue({ entries: [ENTRY], total: 1 });
    renderPage();
    await auditTable();
    const user = userEvent.setup();
    await user.type(screen.getByLabelText("Actor"), "hermes");
    await vi.waitFor(() => {
      const lastCall = mockFetchAuditLog.mock.calls.at(-1)?.[0] as Record<string, unknown>;
      expect(lastCall.actor).toBe("hermes");
    });
  });
});
