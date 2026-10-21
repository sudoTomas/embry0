import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockFetch = vi.fn();
vi.mock("@/api/jobs", () => ({ fetchAgentSession: (...a: unknown[]) => mockFetch(...a) }));
import { TranscriptDrawer } from "../TranscriptDrawer";

function mount(open = true, onClose = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // A factory, not one element: a rerender handed the same element object is skipped.
  const ui = (isOpen: boolean) => (
    <QueryClientProvider client={qc}>
      <TranscriptDrawer open={isOpen} onClose={onClose} jobId="j1" agentType="developer" displayName="Developer" />
    </QueryClientProvider>
  );
  const { rerender } = render(ui(open));
  return { onClose, reopen: (isOpen: boolean) => rerender(ui(isOpen)) };
}

const SESSION = {
  job_id: "j1", agent_type: "developer", mode: "anthropic_api", session_id: "s", last_turn_at: "2026-10-20T12:34:56Z",
  messages: [{ role: "user", content: "Fix the login bug" }],
};

describe("TranscriptDrawer", () => {
  beforeEach(() => { mockFetch.mockReset(); });

  it("renders nothing while closed and fetches nothing", () => {
    mount(false);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("shows the messages and closes on Escape", async () => {
    mockFetch.mockResolvedValue({
      ...SESSION,
      messages: [
        { role: "user", content: "Fix the login bug" },
        { role: "assistant", content: [{ type: "text", text: "On it." }, { type: "tool_use", name: "Edit", input: { file: "a.ts" } }] },
        { role: "user", content: [{ type: "tool_result", tool_use_id: "t", content: "ok" }] },
      ],
    });
    const { onClose } = mount();
    const dialog = await screen.findByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("aria-label"), "the drawer names what it holds: one agent's session for the job").toBe("Stored session of Developer for this job");
    await waitFor(() => expect(dialog.textContent).toContain("Fix the login bug"));
    expect(dialog.textContent).toContain("On it.");
    expect(dialog.textContent).toContain("Edit");
    expect(dialog.textContent, "the filled drawer shows when the session last turned").toContain(new Date("2026-10-20T12:34:56Z").toLocaleString());
    expect(mockFetch).toHaveBeenCalledWith("j1", "developer");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose, "Escape closes the drawer").toHaveBeenCalled();
  });

  it("says when there is no stored session for this agent and job", async () => {
    mockFetch.mockResolvedValue(null);
    mount();
    expect(await screen.findByText("No stored session of Developer for this job.")).toBeTruthy();
  });

  it("says that operator access is needed when the route answers 403", async () => {
    mockFetch.mockRejectedValue({ response: { status: 403, data: { detail: "operate-scope key or embry0-operator role required" } } });
    mount();
    expect(await screen.findByText("Operator access is needed to read stored sessions."), "a 403 must name the tier, not a generic load error").toBeTruthy();
  });

  it("moves focus to the close button on open and gives it back on close", async () => {
    mockFetch.mockResolvedValue(SESSION);
    const opener = document.createElement("button");
    opener.textContent = "Full transcript";
    document.body.appendChild(opener);
    opener.focus();
    const { reopen } = mount();
    const close = await screen.findByRole("button", { name: "Close" });
    expect(document.activeElement, "opening the drawer must move focus into it").toBe(close);
    reopen(false);
    expect(document.activeElement, "closing the drawer must give focus back to the opener").toBe(opener);
    opener.remove();
  });

  it("renders a message whose content is neither text nor blocks without throwing", async () => {
    mockFetch.mockResolvedValue({ ...SESSION, messages: [{ role: "user", content: { odd: "shape" } }, { role: "assistant", content: "fine" }] });
    mount();
    const dialog = await screen.findByRole("dialog");
    await waitFor(() => expect(dialog.textContent, "the well-formed message after the odd one must render").toContain("fine"));
    expect(dialog.textContent).toContain("user");
  });
});
