// When the sign-in check fails, the sign-in screen says so once: the "Sign-in
// is unavailable" card, without the api client's server-error toast under it.
// The real client and its interceptor run here; only the transport is faked.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError, type InternalAxiosRequestConfig } from "axios";

const { toastError } = vi.hoisted(() => ({ toastError: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: toastError, success: vi.fn() } }));

import { api } from "@/api/client";
import { AuthGate } from "../AuthGate";
import { useAuthStore } from "@/stores/authStore";

function refuse(config: InternalAxiosRequestConfig, status: number): never {
  throw new AxiosError(`Request failed with status code ${status}`, String(status), config, undefined, {
    data: { detail: "unavailable" }, status, statusText: "", headers: {}, config,
  });
}

/** The sign-in check fails as given; the key probe answers 401 (no stored key). */
function fakeServer(check: (config: InternalAxiosRequestConfig) => never) {
  api.defaults.adapter = async (config) => {
    if (config.url === "/auth/check") return check(config);
    return refuse(config, 401);
  };
}

function renderGate() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AuthGate>
        <div data-testid="app-content">the app</div>
      </AuthGate>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  toastError.mockClear();
  window.localStorage.clear();
  useAuthStore.setState({
    status: "checking", sessionExpired: false, authMode: null, user: null, ssoEnabled: false, apiKeyLogin: true,
  });
});

describe("a failed sign-in check", () => {
  it("shows the unavailable card and no server-error toast when the check answers 503", async () => {
    fakeServer((config) => refuse(config, 503));
    renderGate();
    expect(await screen.findByText(/Sign-in is unavailable/)).toBeInTheDocument();
    expect(toastError, "the card already says the server did not answer").not.toHaveBeenCalled();
  });

  it("shows the unavailable card and no network toast when the check cannot reach the server", async () => {
    fakeServer((config) => {
      throw new AxiosError("Network Error", "ERR_NETWORK", config);
    });
    renderGate();
    expect(await screen.findByText(/Sign-in is unavailable/)).toBeInTheDocument();
    expect(toastError).not.toHaveBeenCalled();
  });
});
