import { describe, it, expect, vi, beforeEach } from "vitest";

const mockGet = vi.fn();
vi.mock("../client", () => ({
  api: { get: (...args: unknown[]) => mockGet(...args), post: vi.fn() },
}));

import { fetchJobs, fetchJob } from "../jobs";

beforeEach(() => {
  mockGet.mockReset();
  mockGet.mockResolvedValue({ data: { jobs: [], total: 0, offset: 0, limit: 20 } });
});

describe("jobs api", () => {
  it("asks the list for brief tasks alongside the filters", async () => {
    await fetchJobs({ status: "running", limit: 20, offset: 40 });
    expect(mockGet).toHaveBeenCalledWith("/jobs", {
      params: { status: "running", limit: 20, offset: 40, brief: true },
    });
  });

  it("fetches one job in full, without brief", async () => {
    mockGet.mockResolvedValue({ data: { job_id: "job-1" } });
    await fetchJob("job-1");
    expect(mockGet).toHaveBeenCalledWith("/jobs/job-1");
  });
});
