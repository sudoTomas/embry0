import { describe, it, expect, vi, beforeEach } from "vitest";
import type { InternalAxiosRequestConfig } from "axios";
import { AxiosError } from "axios";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

const YAML = "version: 2\nqa_required: never\n";

beforeEach(() => {
  vi.resetModules();
});

async function withCapturingAdapter(responseData: unknown = {}) {
  const { api } = await import("../client");
  const captured: { config?: InternalAxiosRequestConfig } = {};
  api.defaults.adapter = async (config) => {
    captured.config = config;
    return { data: responseData, status: 200, statusText: "OK", headers: {}, config };
  };
  return captured;
}

async function withRejectingAdapter(status: number) {
  const { api } = await import("../client");
  api.defaults.adapter = async (config) => {
    throw new AxiosError(
      `Request failed with status code ${status}`,
      String(status),
      config,
      undefined,
      { data: { detail: "nope" }, status, statusText: "", headers: {}, config },
    );
  };
}

describe("qaConfig api", () => {
  it("GET requests raw text with an identity transform and returns the YAML string", async () => {
    const captured = await withCapturingAdapter(YAML);
    const { fetchQaConfig } = await import("../qaConfig");

    const result = await fetchQaConfig("owner/name");

    expect(result).toBe(YAML);
    expect(captured.config?.url).toBe("/repos/owner/name/-/qa-config");
    expect(captured.config?.responseType).toBe("text");
    // The identity transform must be installed or axios JSON-parses the YAML.
    expect(captured.config?.transformResponse).toHaveLength(1);
  });

  it("GET resolves null on 404 (repo not managed by the external store)", async () => {
    await withRejectingAdapter(404);
    const { fetchQaConfig } = await import("../qaConfig");

    await expect(fetchQaConfig("owner/name")).resolves.toBeNull();
  });

  it("GET rethrows non-404 errors", async () => {
    await withRejectingAdapter(500);
    const { fetchQaConfig } = await import("../qaConfig");

    await expect(fetchQaConfig("owner/name")).rejects.toThrow();
  });

  it("PUT sends the raw YAML body as application/yaml with the toast opt-out", async () => {
    const captured = await withCapturingAdapter({ repo: "owner/name", updated_at: "2026-08-06T00:00:00Z" });
    const { saveQaConfig } = await import("../qaConfig");

    const result = await saveQaConfig("owner/name", YAML);

    expect(result).toEqual({ repo: "owner/name", updated_at: "2026-08-06T00:00:00Z" });
    expect(captured.config?.url).toBe("/repos/owner/name/-/qa-config");
    expect(captured.config?.method).toBe("put");
    // Raw string body — axios must not JSON.stringify it.
    expect(captured.config?.data).toBe(YAML);
    expect(captured.config?.headers["Content-Type"]).toBe("application/yaml");
    expect(captured.config?.suppressValidationToast).toBe(true);
  });

  it("POST check sends the raw text the way PUT does and returns the result", async () => {
    const result = { ok: true, problems: [], warnings: [], summary: null };
    const captured = await withCapturingAdapter(result);
    const { checkQaConfig } = await import("../qaConfig");

    await expect(checkQaConfig("acme/group/widgets", YAML)).resolves.toEqual(result);
    expect(captured.config?.url).toBe("/repos/acme/group/widgets/-/qa-config/check");
    expect(captured.config?.method).toBe("post");
    expect(captured.config?.data).toBe(YAML);
    expect(captured.config?.headers["Content-Type"]).toBe("application/yaml");
    expect(captured.config?.suppressValidationToast).toBe(true);
  });

  it("DELETE hits the qa-config path", async () => {
    const captured = await withCapturingAdapter();
    const { deleteQaConfig } = await import("../qaConfig");

    await deleteQaConfig("owner/name");

    expect(captured.config?.url).toBe("/repos/owner/name/-/qa-config");
    expect(captured.config?.method).toBe("delete");
  });
});
