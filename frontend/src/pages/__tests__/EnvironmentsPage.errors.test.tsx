// A refused environment write shows the API's reason, once: the api client
// toasts every 5xx itself, and the page explains every other refusal with the
// API's detail. Drives the real Environments page, hooks and api client; only
// the HTTP answer is stubbed at the axios adapter.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError } from "axios";

const { toastError } = vi.hoisted(() => ({ toastError: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: toastError, success: vi.fn(), info: vi.fn() } }));
vi.mock("@/components/environments/DetectBanner", () => ({ DetectBanner: () => null }));
vi.mock("@/components/environments/RepoPreferencesSection", () => ({ RepoPreferencesSection: () => null }));
vi.mock("@/components/environments/PullRequestReviewCard", () => ({ PullRequestReviewCard: () => null }));

import { api } from "@/api/client";
import { useAuthStore } from "@/stores/authStore";
import { EnvironmentsPage } from "@/pages/EnvironmentsPage";

const GLOBAL = [{ key: "LOG_LEVEL", value: "info", var_type: "config", description: "", required: false, scope: "app" }];

function answerWrite(status: number, detail: unknown) {
  api.defaults.adapter = async (config) => {
    if (config.method === "get") {
      const data = config.url === "/environment/global" ? { variables: GLOBAL } : [];
      return { data, status: 200, statusText: "OK", headers: {}, config };
    }
    throw new AxiosError(`Request failed with status code ${status}`, String(status), config, undefined, {
      data: { detail }, status, statusText: "", headers: {}, config,
    });
  };
}

async function deleteVariable() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <EnvironmentsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Delete variable" }));
  await waitFor(() => expect(toastError).toHaveBeenCalled());
  await new Promise((r) => setTimeout(r, 20));
  return toastError.mock.calls.map((c) => c[0]);
}

beforeEach(() => {
  toastError.mockClear();
  useAuthStore.setState({ status: "authenticated", authMode: "key", user: null });
});

describe("a refused environment write", () => {
  it("a 500 shows its reason in one toast, not two", async () => {
    answerWrite(500, "database unavailable");
    expect(await deleteVariable()).toEqual(["Server error (500): database unavailable"]);
  });

  it("a 409 shows the API's reason, not the status code", async () => {
    answerWrite(409, "The variables changed since you loaded them");
    expect(await deleteVariable()).toEqual(["Failed: The variables changed since you loaded them"]);
  });

  it("a 422 shows the field and the reason once, without pydantic's 'Value error' prefix", async () => {
    // The environment writes opt out of the client's 422 toast, so the page reports it.
    answerWrite(422, [
      { type: "value_error", loc: ["body", "variables", 0, "key"], msg: "Value error, Key 'LOG_LEVEL' is reserved.", input: "x" },
    ]);
    expect(await deleteVariable()).toEqual(["Failed: variables.0.key: Key 'LOG_LEVEL' is reserved."]);
  });
});
