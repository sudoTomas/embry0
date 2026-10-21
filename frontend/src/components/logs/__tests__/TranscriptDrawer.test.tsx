import { describe, expect, it, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const mockFetch = vi.fn();
vi.mock("@/api/jobs", () => ({ fetchAgentSession: (...a: unknown[]) => mockFetch(...a) }));
import { TranscriptDrawer } from "../TranscriptDrawer";

function mount(open = true, onClose = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <TranscriptDrawer open={open} onClose={onClose} jobId="j1" agentType="developer" displayName="Developer" />
    </QueryClientProvider>,
  );
  return onClose;
}

describe("TranscriptDrawer", () => {
  beforeEach(() => { mockFetch.mockReset(); });

  it("renders nothing while closed and fetches nothing", () => {
    mount(false);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(mockFetch).not.toHaveBeenCalled();
  });

  it("shows the messages and closes on Escape", async () => {
    mockFetch.mockResolvedValue({
      job_id: "j1", agent_type: "developer", mode: "anthropic_api", session_id: "s", last_turn_at: null,
      messages: [
        { role: "user", content: "Fix the login bug" },
        { role: "assistant", content: [{ type: "text", text: "On it." }, { type: "tool_use", name: "Edit", input: { file: "a.ts" } }] },
        { role: "user", content: [{ type: "tool_result", tool_use_id: "t", content: "ok" }] },
      ],
    });
    const onClose = mount();
    const dialog = await screen.findByRole("dialog");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    expect(dialog.getAttribute("aria-label")).toBe("Full transcript: Developer");
    await waitFor(() => expect(dialog.textContent).toContain("Fix the login bug"));
    expect(dialog.textContent).toContain("On it.");
    expect(dialog.textContent).toContain("Edit");
    expect(mockFetch).toHaveBeenCalledWith("j1", "developer");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose, "Escape closes the drawer").toHaveBeenCalled();
  });

  it("says when there is no stored transcript", async () => {
    mockFetch.mockResolvedValue(null);
    mount();
    expect(await screen.findByText(/no stored transcript/i)).toBeTruthy();
  });
});
