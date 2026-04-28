import { describe, it, expect, vi, beforeEach } from "vitest";

// client.ts runs the Authorization header assignment at module load time, so
// each test must reset the module registry and re-import to observe the
// effect of different VITE_API_KEY values.

describe("api client — Authorization header", () => {
  beforeEach(() => {
    vi.resetModules();
    vi.unstubAllEnvs();
  });

  it("sets Authorization: Bearer <key> when VITE_API_KEY is defined", async () => {
    vi.stubEnv("VITE_API_KEY", "my-secret-key");
    const { api } = await import("../client");
    expect(api.defaults.headers.common["Authorization"]).toBe("Bearer my-secret-key");
  });

  it("does not set Authorization header when VITE_API_KEY is absent", async () => {
    // Ensure the env var is not set
    vi.stubEnv("VITE_API_KEY", "");
    const { api } = await import("../client");
    expect(api.defaults.headers.common["Authorization"]).toBeUndefined();
  });
});
