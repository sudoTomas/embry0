import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { toast } from "sonner";
import type { HistoryEntry, RepoHistory } from "@/api/repoHistory";
import { formatDateTime } from "@/lib/utils";

const mockFetchHistory = vi.fn();
const mockUndoChange = vi.fn();
vi.mock("@/api/repoHistory", () => ({
  fetchHistory: (...a: unknown[]) => mockFetchHistory(...a),
  undoChange: (...a: unknown[]) => mockUndoChange(...a),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { HistoryPanel } from "../HistoryPanel";
import { toastWithUndo } from "@/hooks/useRepoHistory";

const WHY_NOT = "This changed again later. Undo the newer change first.";
const CONFIRM = "Put back how it was before this change? This is recorded too, so you can undo it again.";

function entry(overrides: Partial<HistoryEntry>): HistoryEntry {
  return {
    id: 1,
    surface: "qa_config",
    what: "Test setup created",
    actor: "alex@example.com",
    source: "person",
    created_at: "2026-10-18T10:00:00Z",
    undoable: false,
    why_not: WHY_NOT,
    ...overrides,
  };
}

const HISTORY: RepoHistory = {
  entries: [
    entry({ id: 5, what: "Test setup changed", source: "proposal", undoable: true, why_not: null }),
    entry({
      id: 4,
      surface: "preferences",
      what: "Branch embry0 starts from changed from (repository default) to develop",
      // Someone else's change, as the API answers a caller that is not an admin (as GET /audit shows actors).
      actor: null,
      undoable: true,
      why_not: null,
    }),
    entry({ id: 3, what: "Test setup changed", source: "restore" }),
    entry({ id: 2, what: "Test setup changed", source: "onboard" }),
    entry({ id: 1, what: "Test setup created", actor: "sam@example.com", source: "bootstrap" }),
  ],
  before_history: false,
};

function renderPanel(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  render(
    <QueryClientProvider client={client}>
      <HistoryPanel repo="acme/widgets" />
    </QueryClientProvider>,
  );
  return client;
}

const entries = () => screen.getAllByTestId("history-entry");

beforeEach(() => {
  mockFetchHistory.mockReset();
  mockUndoChange.mockReset();
  vi.mocked(toast.success).mockReset();
  vi.mocked(toast.error).mockReset();
});
afterEach(() => vi.restoreAllMocks());

describe("HistoryPanel", () => {
  it("is the History section the setup card links to", async () => {
    mockFetchHistory.mockResolvedValue(HISTORY);
    renderPanel();
    const panel = screen.getByTestId("history-panel");
    expect(panel).toHaveAttribute("id", "history");
    expect(within(panel).getByRole("heading", { name: "History" })).toBeInTheDocument();
    await screen.findByText("Branch embry0 starts from changed from (repository default) to develop");
    expect(mockFetchHistory).toHaveBeenCalledWith("acme/widgets");
  });

  it("shows each change in plain words: what, who, how and when", async () => {
    mockFetchHistory.mockResolvedValue(HISTORY);
    renderPanel();
    await screen.findAllByTestId("history-entry");
    const [proposal, nobody, restore, onboard, starter] = entries();
    const when = formatDateTime("2026-10-18T10:00:00Z");
    expect(proposal).toHaveTextContent("Test setup changed");
    expect(within(proposal).getByTestId("history-entry-meta")).toHaveTextContent(
      `alex@example.com · Setup proposal · ${when}`,
    );
    expect(within(nobody).getByTestId("history-entry-meta")).toHaveTextContent(`Someone else · Dashboard or API · ${when}`);
    expect(within(restore).getByTestId("history-entry-meta")).toHaveTextContent("· Undo ·");
    expect(within(onboard).getByTestId("history-entry-meta")).toHaveTextContent("· embry0 setup ·");
    expect(within(starter).getByTestId("history-entry-meta")).toHaveTextContent(`sam@example.com · Starter · ${when}`);
  });

  it("offers Undo only where it can work, and says why elsewhere", async () => {
    mockFetchHistory.mockResolvedValue(HISTORY);
    renderPanel();
    await screen.findAllByTestId("history-entry");
    const [proposal, , restore] = entries();
    const undo = within(proposal).getByRole("button", { name: "Undo this change" });
    expect(undo.className).toContain("border-border-strong");
    expect(within(proposal).queryByText(WHY_NOT)).not.toBeInTheDocument();
    expect(within(restore).queryByRole("button")).not.toBeInTheDocument();
    expect(within(restore).getByText(WHY_NOT)).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Undo this change" })).toHaveLength(2);
  });

  it("asks first, and sends nothing when the person cancels", async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    mockFetchHistory.mockResolvedValue(HISTORY);
    renderPanel();
    await screen.findAllByTestId("history-entry");
    await user.click(within(entries()[0]).getByRole("button", { name: "Undo this change" }));
    expect(confirm).toHaveBeenCalledWith(CONFIRM);
    expect(mockUndoChange).not.toHaveBeenCalled();
  });

  it("undoes on confirm and refreshes everything the change touched", async () => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    mockFetchHistory.mockResolvedValue(HISTORY);
    mockUndoChange.mockResolvedValue({ revision_id: 6 });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const invalidate = vi.spyOn(client, "invalidateQueries");
    renderPanel(client);
    await screen.findAllByTestId("history-entry");
    await user.click(within(entries()[0]).getByRole("button", { name: "Undo this change" }));
    await waitFor(() => expect(mockUndoChange).toHaveBeenCalledWith("acme/widgets", 5));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Change undone."));
    for (const queryKey of [
      ["repo-history", "acme/widgets"],
      ["qa-config", "acme/widgets"],
      ["repo-preferences", "acme/widgets"],
      ["repos", "detail", "acme/widgets"],
      ["repo-readiness", "acme/widgets"],
    ]) {
      expect(invalidate).toHaveBeenCalledWith({ queryKey });
    }
  });

  it.each([
    [409, WHY_NOT],
    [422, "That earlier version is no longer valid: 'boot_comand' is not a setting embry0 knows."],
  ])("shows the server's reason when undo is refused (%i)", async (status, detail) => {
    const user = userEvent.setup();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    mockFetchHistory.mockResolvedValue(HISTORY);
    mockUndoChange.mockRejectedValue({ response: { status, data: { detail } } });
    renderPanel();
    await screen.findAllByTestId("history-entry");
    await user.click(within(entries()[0]).getByRole("button", { name: "Undo this change" }));
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(detail));
    expect(toast.success).not.toHaveBeenCalled();
  });

  it("says when changes from before history was kept are missing", async () => {
    mockFetchHistory.mockResolvedValue({ ...HISTORY, before_history: true });
    renderPanel();
    expect(
      await screen.findByText(
        "Changes made before embry0 kept history are not listed, and who made them is not known.",
      ),
    ).toBeInTheDocument();
  });

  it("leaves that note out when history covers every change", async () => {
    mockFetchHistory.mockResolvedValue(HISTORY);
    renderPanel();
    await screen.findAllByTestId("history-entry");
    expect(screen.queryByText(/before embry0 kept history/)).not.toBeInTheDocument();
  });

  it("says when nothing is recorded yet", async () => {
    mockFetchHistory.mockResolvedValue({ entries: [], before_history: false });
    renderPanel();
    expect(await screen.findByText("No changes recorded yet.")).toBeInTheDocument();
    expect(screen.queryByTestId("history-entry")).not.toBeInTheDocument();
  });

  it("shows both the empty line and the note for a setup older than history", async () => {
    mockFetchHistory.mockResolvedValue({ entries: [], before_history: true });
    renderPanel();
    expect(await screen.findByText("No changes recorded yet.")).toBeInTheDocument();
    expect(screen.getByText(/before embry0 kept history/)).toBeInTheDocument();
  });

  it("says so when the history cannot load, and retries", async () => {
    const user = userEvent.setup();
    mockFetchHistory.mockRejectedValueOnce(new Error("boom")).mockResolvedValue(HISTORY);
    renderPanel();
    expect(await screen.findByText("Could not load the history.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findAllByTestId("history-entry")).toHaveLength(5);
  });

  it("marks the panel busy while the history loads", () => {
    mockFetchHistory.mockReturnValue(new Promise(() => {}));
    renderPanel();
    expect(screen.getByTestId("history-panel")).toHaveAttribute("aria-busy", "true");
  });
});

/** Press the Undo button on the first success toast. */
function clickToastUndo() {
  const action = vi.mocked(toast.success).mock.calls[0][1]?.action;
  if (!action || typeof action !== "object" || !("onClick" in action)) throw new Error("the toast has no Undo action");
  action.onClick({} as Parameters<typeof action.onClick>[0]);
}

describe("toastWithUndo", () => {
  it("puts an Undo button on the toast that undoes the change", async () => {
    mockUndoChange.mockResolvedValue({ revision_id: 9 });
    const client = new QueryClient();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    toastWithUndo("Testing is set up.", "acme/widgets", 8, client);
    expect(toast.success).toHaveBeenCalledWith("Testing is set up.", {
      action: { label: "Undo", onClick: expect.any(Function) },
    });
    clickToastUndo();
    await waitFor(() => expect(mockUndoChange).toHaveBeenCalledWith("acme/widgets", 8));
    await waitFor(() => expect(toast.success).toHaveBeenCalledWith("Change undone."));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["repo-history", "acme/widgets"] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["repo-readiness", "acme/widgets"] });
  });

  it("says why when the undo from the toast is refused", async () => {
    mockUndoChange.mockRejectedValue({ response: { status: 409, data: { detail: WHY_NOT } } });
    toastWithUndo("Testing is set up.", "acme/widgets", 8, new QueryClient());
    clickToastUndo();
    await waitFor(() => expect(toast.error).toHaveBeenCalledWith(WHY_NOT));
  });

  it("shows a plain toast when nothing changed", () => {
    toastWithUndo("Testing is set up.", "acme/widgets", null, new QueryClient());
    expect(toast.success).toHaveBeenCalledWith("Testing is set up.");
  });
});
