import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AxiosError } from "axios";

const mockGet = vi.fn();
const mockPost = vi.fn();
vi.mock("@/api/client", () => ({
  api: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
}));

import { AuthGate } from "../AuthGate";
import { useAuthStore } from "@/stores/authStore";

function axiosErr(status: number, code = String(status)): AxiosError {
  return new AxiosError("err", code, undefined, undefined, {
    data: { detail: "err" },
    status,
    statusText: "",
    headers: {},
    config: {} as never,
  });
}

/**
 * Route the mocked api.get by path. The gate now probes /auth/check first
 * (RAV-1069) and falls back to the /queue key probe — positional mock
 * queues would silently couple tests to call order.
 */
function routeGet(routes: Record<string, () => Promise<unknown>>) {
  mockGet.mockImplementation((path: unknown) => {
    const handler = routes[String(path)];
    if (!handler) return Promise.reject(axiosErr(404, "ERR_BAD_REQUEST"));
    return handler();
  });
}

function renderGate() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AuthGate>
        <div data-testid="app-content">the app</div>
      </AuthGate>
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mockGet.mockReset();
  mockPost.mockReset();
  window.localStorage.clear();
  useAuthStore.setState({
    status: "checking",
    sessionExpired: false,
    authMode: null,
    user: null,
    ssoEnabled: false,
    apiKeyLogin: true,
  });
});

describe("AuthGate (key mode — pre-SSO behavior preserved)", () => {
  it("renders the app when the key probe succeeds", async () => {
    routeGet({ "/queue": () => Promise.resolve({ data: {} }) });
    renderGate();
    expect(await screen.findByTestId("app-content")).toBeInTheDocument();
  });

  it("shows the key prompt when the probe 401s", async () => {
    routeGet({ "/queue": () => Promise.reject(axiosErr(401)) });
    renderGate();
    expect(await screen.findByLabelText("API key")).toBeInTheDocument();
    expect(screen.queryByTestId("app-content")).toBeNull();
  });

  it("renders the app when the probe fails for non-auth reasons (backend down)", async () => {
    routeGet({ "/queue": () => Promise.reject(new AxiosError("Network Error", "ERR_NETWORK")) });
    renderGate();
    expect(await screen.findByTestId("app-content")).toBeInTheDocument();
  });

  it("stores a validated key and unlocks the app", async () => {
    routeGet({ "/queue": () => Promise.reject(axiosErr(401)) });
    renderGate();

    const user = userEvent.setup();
    const input = await screen.findByLabelText("API key");
    // Key validation goes through api.get with an explicit header.
    mockGet.mockImplementation((path: unknown, opts?: unknown) => {
      if (String(path) === "/queue" && opts) return Promise.resolve({ data: {} });
      return Promise.reject(axiosErr(401));
    });
    await user.type(input, "sk-valid");
    await user.click(screen.getByRole("button", { name: /unlock/i }));

    expect(await screen.findByTestId("app-content")).toBeInTheDocument();
    expect(window.localStorage.getItem("embry0.api-key")).toBe("sk-valid");
    expect(mockGet).toHaveBeenLastCalledWith("/queue", {
      headers: { Authorization: "Bearer sk-valid" },
    });
  });

  it("shows a clear error for a rejected key and stays locked", async () => {
    routeGet({ "/queue": () => Promise.reject(axiosErr(401)) });
    renderGate();

    const user = userEvent.setup();
    await user.type(await screen.findByLabelText("API key"), "sk-wrong");
    await user.click(screen.getByRole("button", { name: /unlock/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/invalid api key/i);
    expect(screen.queryByTestId("app-content")).toBeNull();
    expect(window.localStorage.getItem("embry0.api-key")).toBeNull();
  });
});

describe("AuthGate (SSO — RAV-1069)", () => {
  it("renders the app for an operator session without any key probe", async () => {
    routeGet({
      "/auth/check": () =>
        Promise.resolve({ data: { sso_enabled: true, authenticated: true, tier: "operator" } }),
      "/auth/me": () =>
        Promise.resolve({ data: { email: "op@example.com", name: "Op", tier: "operator" } }),
    });
    renderGate();
    expect(await screen.findByTestId("app-content")).toBeInTheDocument();
    expect(useAuthStore.getState().authMode).toBe("session");
    expect(useAuthStore.getState().user?.email).toBe("op@example.com");
    // The key probe never ran.
    expect(mockGet).not.toHaveBeenCalledWith("/queue");
  });

  it("shows access-denied for a session with no embry0 role (default-deny)", async () => {
    routeGet({
      "/auth/check": () =>
        Promise.resolve({ data: { sso_enabled: true, authenticated: true, tier: "none" } }),
      "/auth/me": () =>
        Promise.resolve({ data: { email: "norole@example.com", name: "N", tier: "none" } }),
    });
    renderGate();
    expect(await screen.findByText(/access denied/i)).toBeInTheDocument();
    expect(screen.getByText("norole@example.com")).toBeInTheDocument();
    expect(screen.queryByTestId("app-content")).toBeNull();
    expect(screen.queryByLabelText("API key")).toBeNull();
  });

  it("redirects straight to the IdP login when the backend has SSO configured", async () => {
    // No in-repo login screen on SSO deployments: the IdP's own (per-tenant
    // brandable) page is the only login surface. Customer copy never lives here.
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    routeGet({
      "/auth/check": () =>
        Promise.resolve({ data: { sso_enabled: true, authenticated: false, tier: null } }),
      "/queue": () => Promise.reject(axiosErr(401)),
    });
    renderGate();
    await screen.findByText(/redirecting to sign-in/i);
    expect(assign).toHaveBeenCalledWith("/api/v1/auth/login");
    expect(screen.queryByLabelText("API key")).toBeNull();
    expect(screen.queryByRole("button", { name: /sso/i })).toBeNull();
    vi.unstubAllGlobals();
  });

  it("renders the neutral API-key form when the backend has no SSO configured", async () => {
    routeGet({
      "/auth/check": () =>
        Promise.resolve({ data: { sso_enabled: false, authenticated: false, tier: null } }),
      "/queue": () => Promise.reject(axiosErr(401)),
    });
    renderGate();
    expect(await screen.findByLabelText("API key")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /sso/i })).toBeNull();
    // Names that must never ship are checked by scripts/check_denylist.py,
    // whose terms live outside the repository (docs/ops/verification.md).
  });

  it("does not redirect the access-denied state (default-deny must not loop)", async () => {
    const assign = vi.fn();
    vi.stubGlobal("location", { ...window.location, assign });
    routeGet({
      "/auth/check": () =>
        Promise.resolve({ data: { sso_enabled: true, authenticated: true, tier: "none" } }),
      "/auth/me": () =>
        Promise.resolve({ data: { email: "x@y.z", name: "X", tier: "none" } }),
    });
    renderGate();
    await screen.findByText(/access denied/i);
    expect(assign).not.toHaveBeenCalled();
    vi.unstubAllGlobals();
  });
});

describe("AuthGate (API-key sign-in turned off for people)", () => {
  it("shows no key form when the server says key login is off and SSO is down", async () => {
    // The public origin is SSO-only for people (api_key_login false). With
    // SSO reporting off, the key form must not appear there.
    routeGet({
      "/auth/check": () =>
        Promise.resolve({ data: { sso_enabled: false, authenticated: false, tier: null, api_key_login: false } }),
      "/queue": () => Promise.reject(axiosErr(401)),
    });
    renderGate();
    expect(await screen.findByText(/Sign-in is unavailable/)).toBeInTheDocument();
    expect(screen.queryByLabelText("API key")).toBeNull();
    expect(screen.queryByTestId("app-content")).toBeNull();
  });

  it("keeps the key form where the server allows key login", async () => {
    routeGet({
      "/auth/check": () =>
        Promise.resolve({ data: { sso_enabled: false, authenticated: false, tier: null, api_key_login: true } }),
      "/queue": () => Promise.reject(axiosErr(401)),
    });
    renderGate();
    expect(await screen.findByLabelText("API key")).toBeInTheDocument();
  });
});

describe("AuthGate (the sign-in check itself fails)", () => {
  // Only the server knows whether this origin lets people in with a key. When
  // /auth/check cannot answer, the key form must not appear on an origin that
  // may be SSO-only: the prompt says sign-in is unavailable, with Try again.
  it("shows sign-in unavailable, not the key form, when the check answers 5xx", async () => {
    routeGet({
      "/auth/check": () => Promise.reject(axiosErr(503)),
      "/queue": () => Promise.reject(axiosErr(401)),
    });
    renderGate();
    expect(await screen.findByText(/Sign-in is unavailable/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.queryByLabelText("API key")).toBeNull();
  });

  it("shows sign-in unavailable when the check cannot reach the server", async () => {
    routeGet({
      "/auth/check": () => Promise.reject(new AxiosError("Network Error", "ERR_NETWORK")),
      "/queue": () => Promise.reject(axiosErr(401)),
    });
    renderGate();
    expect(await screen.findByText(/Sign-in is unavailable/)).toBeInTheDocument();
    expect(screen.queryByLabelText("API key")).toBeNull();
  });

  it("keeps the key form on an older backend that has no sign-in check (404)", async () => {
    routeGet({
      "/auth/check": () => Promise.reject(axiosErr(404)),
      "/queue": () => Promise.reject(axiosErr(401)),
    });
    renderGate();
    expect(await screen.findByLabelText("API key")).toBeInTheDocument();
  });

  it("still renders the app when a stored key works, whatever the check said", async () => {
    routeGet({
      "/auth/check": () => Promise.reject(axiosErr(503)),
      "/queue": () => Promise.resolve({ data: {} }),
    });
    renderGate();
    expect(await screen.findByTestId("app-content")).toBeInTheDocument();
  });
});
