import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import type { AxiosInstance, InternalAxiosRequestConfig } from "axios";
import { AxiosError, AxiosHeaders } from "axios";

// The Authorization header is resolved per-request from localStorage only
// (RAV-1058: the build-time VITE_API_KEY bake was removed). Each test resets
// the module registry so client.ts + authStore re-import with a clean
// interceptor/auth state.

const STORAGE_KEY = "embry0.api-key";

const { toastError } = vi.hoisted(() => ({ toastError: vi.fn() }));
vi.mock("sonner", () => ({ toast: { error: toastError, success: vi.fn() } }));

/** Install a capturing adapter that resolves every request with 200 {}. */
function captureRequests(api: AxiosInstance): { config?: InternalAxiosRequestConfig } {
  const captured: { config?: InternalAxiosRequestConfig } = {};
  api.defaults.adapter = async (config) => {
    captured.config = config;
    return { data: {}, status: 200, statusText: "OK", headers: {}, config };
  };
  return captured;
}

/** Install an adapter that rejects every request with the given HTTP status. */
function rejectRequests(api: AxiosInstance, status: number, detail: unknown = "nope"): void {
  api.defaults.adapter = async (config) => {
    const error = new AxiosError(
      `Request failed with status code ${status}`,
      String(status),
      config,
      undefined,
      {
        data: { detail },
        status,
        statusText: "",
        headers: {},
        config,
      },
    );
    throw error;
  };
}

beforeEach(() => {
  vi.resetModules();
  vi.unstubAllEnvs();
  window.localStorage.clear();
  toastError.mockClear();
});

afterEach(() => {
  window.localStorage.clear();
});

describe("api client — runtime Authorization header", () => {
  it("uses the localStorage key when present", async () => {
    window.localStorage.setItem(STORAGE_KEY, "stored-key");
    const { api } = await import("../client");
    const captured = captureRequests(api);

    await api.get("/queue");
    expect(captured.config?.headers.Authorization).toBe("Bearer stored-key");
  });

  it("ignores the build-time VITE_API_KEY — no baked-in fallback (RAV-1058)", async () => {
    // Even if a stale build somehow inlined a key, the client must not use it:
    // the source of truth is localStorage only, so an unset browser prompts.
    vi.stubEnv("VITE_API_KEY", "build-time-key");
    const { api } = await import("../client");
    const captured = captureRequests(api);

    await api.get("/queue");
    expect(captured.config?.headers.Authorization).toBeUndefined();
  });

  it("sends no Authorization header when no key is stored", async () => {
    const { api } = await import("../client");
    const captured = captureRequests(api);

    await api.get("/queue");
    expect(captured.config?.headers.Authorization).toBeUndefined();
  });

  it("keeps an explicit per-request Authorization header (AuthGate key validation)", async () => {
    window.localStorage.setItem(STORAGE_KEY, "stored-key");
    const { api } = await import("../client");
    const captured = captureRequests(api);

    await api.get("/queue", { headers: new AxiosHeaders({ Authorization: "Bearer candidate" }) });
    expect(captured.config?.headers.Authorization).toBe("Bearer candidate");
  });

  it("picks up a key stored after module load — no reload required", async () => {
    const { api } = await import("../client");
    const { setStoredApiKey } = await import("@/lib/apiKey");
    const captured = captureRequests(api);

    await api.get("/queue");
    expect(captured.config?.headers.Authorization).toBeUndefined();

    setStoredApiKey("late-key");
    await api.get("/queue");
    expect(captured.config?.headers.Authorization).toBe("Bearer late-key");
  });
});

describe("api client — 401 handling", () => {
  it("flips the auth store to prompt on a 401 response", async () => {
    const { api } = await import("../client");
    const { useAuthStore } = await import("@/stores/authStore");
    rejectRequests(api, 401);

    useAuthStore.setState({ status: "authenticated", sessionExpired: false });
    await expect(api.get("/queue")).rejects.toThrow();

    expect(useAuthStore.getState().status).toBe("prompt");
    expect(useAuthStore.getState().sessionExpired).toBe(true);
  });

  it("does not touch the auth store on non-401 errors", async () => {
    const { api } = await import("../client");
    const { useAuthStore } = await import("@/stores/authStore");
    rejectRequests(api, 500);

    useAuthStore.setState({ status: "authenticated", sessionExpired: false });
    await expect(api.get("/queue")).rejects.toThrow();

    expect(useAuthStore.getState().status).toBe("authenticated");
  });
});

describe("api client — 422 toast suppression", () => {
  it("toasts a validation error by default", async () => {
    const { api } = await import("../client");
    rejectRequests(api, 422);

    await expect(api.get("/queue")).rejects.toThrow();
    expect(toastError).toHaveBeenCalledWith("Validation error: nope");
  });

  it("skips the toast when the request opts out via suppressValidationToast", async () => {
    const { api } = await import("../client");
    rejectRequests(api, 422);

    await expect(api.get("/queue", { suppressValidationToast: true })).rejects.toThrow();
    expect(toastError).not.toHaveBeenCalled();
  });
});

describe("api client — readable error details (#561)", () => {
  it("renders a FastAPI 422 detail list as field: msg lines, not [object Object]", async () => {
    const { api } = await import("../client");
    rejectRequests(api, 422, [
      { type: "value_error", loc: ["body", "cap_add"], msg: "Value error, cap_add: must be empty", input: ["x"] },
      { type: "string_type", loc: ["body", "name"], msg: "Input should be a valid string", input: 5 },
    ]);

    await expect(api.post("/sandbox-profiles", {})).rejects.toThrow();
    expect(
      toastError,
      "a 422 detail list must be shown as its messages, not interpolated as [object Object]",
    ).toHaveBeenCalledWith(
      "Validation error: cap_add: cap_add: must be empty; name: Input should be a valid string",
    );
  });

  it("renders a list detail on a 5xx too", async () => {
    const { api } = await import("../client");
    rejectRequests(api, 500, [{ loc: ["body"], msg: "boom" }]);

    await expect(api.get("/queue")).rejects.toThrow();
    expect(toastError, "a 5xx detail list must be shown as its messages").toHaveBeenCalledWith(
      "Server error (500): boom",
    );
  });

  it("falls back to the generic text when the detail has no readable message", async () => {
    const { api } = await import("../client");
    rejectRequests(api, 422, { unexpected: true });

    await expect(api.get("/queue")).rejects.toThrow();
    expect(toastError, "an unreadable detail must fall back, not print [object Object]").toHaveBeenCalledWith(
      "Validation error: invalid request",
    );
  });
});
