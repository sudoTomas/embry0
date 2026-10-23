// When the API refuses a write, the operator reads the API's reason, once:
// the client toasts a 422, a 5xx and a network failure itself, and the page
// explains every other refusal (403, 409) with the API's detail.
// Drives the real Agents page, hooks and api client; only the HTTP answer is
// stubbed at the axios adapter.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError } from "axios";

const { toastError } = vi.hoisted(() => ({ toastError: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: toastError, success: vi.fn(), info: vi.fn() } }));

import { api } from "@/api/client";
import { useAuthStore } from "@/stores/authStore";
import { AgentsPage } from "@/pages/AgentsPage";

const AGENTS = [
  { type: "helper", description: "Custom", model: "claude-opus-5", tools: [], skills: [], system_prompt: "",
    is_builtin: false, provider: "anthropic", provider_ready: true },
];

function answerDelete(status: number, detail: unknown) {
  api.defaults.adapter = async (config) => {
    if (config.method === "get") return { data: AGENTS, status: 200, statusText: "OK", headers: {}, config };
    throw new AxiosError(`Request failed with status code ${status}`, String(status), config, undefined, {
      data: { detail }, status, statusText: "", headers: {}, config,
    });
  };
}

async function deleteHelper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <AgentsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  // Every test renders the page cold; under load that outruns the default 1 s wait (#106).
  fireEvent.click(await screen.findByRole("button", { name: /Delete/ }, { timeout: 4000 }));
  fireEvent.click(screen.getByRole("button", { name: /Confirm delete/ }));
}

beforeEach(() => {
  toastError.mockClear();
  useAuthStore.setState({ status: "authenticated", authMode: "key", user: null });
});

describe("a refused write", () => {
  it("a 409 shows the API's reason, not the status code", async () => {
    answerDelete(409, "Agent helper is used by 2 pipeline templates");
    await deleteHelper();
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    expect(toastError.mock.calls.map((c) => c[0])).toEqual([
      "Failed to delete: Agent helper is used by 2 pipeline templates",
    ]);
  });

  it("a 500 shows its reason in one toast, not two", async () => {
    // The client reports every 5xx itself; the page must not repeat it.
    answerDelete(500, "database unavailable");
    await deleteHelper();
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(toastError.mock.calls.map((c) => c[0])).toEqual(["Server error (500): database unavailable"]);
  });

  it("a 422 detail list shows its field messages in one toast", async () => {
    // The client reports a 422 (unless the request opts out); the page must not repeat it.
    answerDelete(422, [{ type: "value_error", loc: ["path", "type"], msg: "unknown agent type", input: "x" }]);
    await deleteHelper();
    await waitFor(() => expect(toastError).toHaveBeenCalled());
    await new Promise((r) => setTimeout(r, 20));
    expect(toastError.mock.calls.map((c) => c[0])).toEqual(["Validation error: type: unknown agent type"]);
  });
});
