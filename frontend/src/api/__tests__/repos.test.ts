import { describe, it, expect, vi, beforeEach } from "vitest";
import type { InternalAxiosRequestConfig } from "axios";

vi.mock("sonner", () => ({ toast: { error: vi.fn(), success: vi.fn() } }));

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

const ROW = {
  repo: "acme/widgets",
  active: true,
  notes: "",
  created_at: "2026-08-06T00:00:00Z",
  updated_at: "2026-08-06T00:00:00Z",
  forge: "github" as const,
  has_qa_config: true,
};

describe("repos api (RAV-1251)", () => {
  it("fetchRepos hits GET /repos without params by default", async () => {
    const captured = await withCapturingAdapter([ROW]);
    const { fetchRepos } = await import("../repos");
    const rows = await fetchRepos();
    expect(captured.config?.url).toBe("/repos");
    expect(captured.config?.params).toBeUndefined();
    expect(rows[0].repo).toBe("acme/widgets");
  });

  it("fetchRepos passes include_inactive when asked", async () => {
    const captured = await withCapturingAdapter([]);
    const { fetchRepos } = await import("../repos");
    await fetchRepos(true);
    expect(captured.config?.params).toEqual({ include_inactive: true });
  });

  it("registerRepo POSTs the repo + notes", async () => {
    const captured = await withCapturingAdapter(ROW);
    const { registerRepo } = await import("../repos");
    const row = await registerRepo("acme/widgets", "pilot");
    expect(captured.config?.method).toBe("post");
    expect(captured.config?.url).toBe("/repos");
    // forge defaults to github so every pre-#162 caller keeps working (#169).
    expect(JSON.parse(captured.config?.data as string)).toEqual({
      repo: "acme/widgets",
      notes: "pilot",
      forge: "github",
    });
    expect(row.repo).toBe("acme/widgets");
  });

  it("fetchRepoDetail hits the full repo path", async () => {
    const captured = await withCapturingAdapter({ ...ROW, env_var_count: 2, env_secret_count: 1 });
    const { fetchRepoDetail } = await import("../repos");
    const detail = await fetchRepoDetail("acme/widgets");
    expect(captured.config?.url).toBe("/repos/acme/widgets/-/");
    expect(detail.env_secret_count).toBe(1);
  });

  it("setRepoActive PUTs the active flag", async () => {
    const captured = await withCapturingAdapter({ ...ROW, active: false });
    const { setRepoActive } = await import("../repos");
    const row = await setRepoActive("acme/widgets", false);
    expect(captured.config?.method).toBe("put");
    expect(captured.config?.url).toBe("/repos/acme/widgets/-/active");
    expect(JSON.parse(captured.config?.data as string)).toEqual({ active: false });
    expect(row.active).toBe(false);
  });
  it("addresses a GitLab nested subgroup path (#162)", async () => {
    const captured = await withCapturingAdapter({ ...ROW, repo: "acme/products/fbr" });
    const { fetchRepoDetail } = await import("../repos");
    await fetchRepoDetail("acme/products/fbr");
    // Three segments — the old fetchRepoDetail(owner, name) could not express this.
    expect(captured.config?.url).toBe("/repos/acme/products/fbr/-/");
  });

  it("registers a repo on gitlab when asked (#169)", async () => {
    const captured = await withCapturingAdapter({ ...ROW, repo: "acme/products/fbr", forge: "gitlab" });
    const { registerRepo } = await import("../repos");
    await registerRepo("acme/products/fbr", "", "gitlab");
    expect(JSON.parse(captured.config?.data as string)).toEqual({
      repo: "acme/products/fbr",
      notes: "",
      forge: "gitlab",
    });
  });
});
