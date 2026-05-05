import { describe, it, expect, beforeEach, vi } from "vitest";
import {
  fetchQaAppHistory,
  fetchQaRepos,
  fetchQaRun,
  fetchQaRunApp,
  fetchQaRunsForRepo,
} from "../qaDashboard";
import { api } from "../client";

// Each test stubs axios.get; assertions check the URL + params + the parsed payload.

describe("qaDashboard api client", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("fetchQaRepos hits /qa/repos with default limit=50", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({ data: [] });
    await fetchQaRepos();
    expect(get).toHaveBeenCalledWith("/qa/repos", { params: { limit: 50 } });
  });

  it("fetchQaRepos honours an explicit limit", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({ data: [] });
    await fetchQaRepos(10);
    expect(get).toHaveBeenCalledWith("/qa/repos", { params: { limit: 10 } });
  });

  it("fetchQaRunsForRepo url-encodes the repo segment", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({ data: [] });
    await fetchQaRunsForRepo("org/repo-1", { limit: 25, offset: 5 });
    expect(get).toHaveBeenCalledWith("/qa/repos/org%2Frepo-1/runs", {
      params: { limit: 25, offset: 5 },
    });
  });

  it("fetchQaRunsForRepo defaults pagination", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({ data: [] });
    await fetchQaRunsForRepo("org/r");
    expect(get).toHaveBeenCalledWith("/qa/repos/org%2Fr/runs", {
      params: { limit: 50, offset: 0 },
    });
  });

  it("fetchQaAppHistory url-encodes both segments", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({ data: [] });
    await fetchQaAppHistory("org/repo", "hub", 15);
    expect(get).toHaveBeenCalledWith(
      "/qa/repos/org%2Frepo/apps/hub/history",
      { params: { limit: 15 } },
    );
  });

  it("fetchQaRun returns parsed RunDetail", async () => {
    const fakeDetail = {
      job_id: "j-1",
      repo: "org/r",
      started_at: "2026-01-01T00:00:00Z",
      overall_status: "passed",
      apps: [],
    };
    vi.spyOn(api, "get").mockResolvedValue({ data: fakeDetail });
    const result = await fetchQaRun("j-1");
    expect(result).toEqual(fakeDetail);
  });

  it("fetchQaRunApp encodes app segment", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue({
      data: {
        app_name: "hub",
        status: "passed",
        duration_ms: 0,
        cache_hits: { prebaked_image: false, shared_volume: false, turbo_remote_hits: [], turbo_remote_misses: [] },
        trace_url: null,
        failure_summary: null,
      },
    });
    await fetchQaRunApp("j-1", "hub");
    expect(get).toHaveBeenCalledWith("/qa/runs/j-1/apps/hub");
  });
});
