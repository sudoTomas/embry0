import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

const mockFetchJobs = vi.fn();
const mockFetchJob = vi.fn();
vi.mock("@/api/jobs", () => ({
  fetchJobs: (...a: unknown[]) => mockFetchJobs(...a),
  fetchJob: (...a: unknown[]) => mockFetchJob(...a),
}));
const mockStartAudit = vi.fn();
vi.mock("@/api/standards", async () => {
  const actual = await vi.importActual<typeof import("@/api/standards")>("@/api/standards");
  return { ...actual, startStandardAudit: (...a: unknown[]) => mockStartAudit(...a) };
});

import { AUDIT_POLL_MS, UNFINISHED_JOBS_KEY, useAuditJobStatuses, useStartAudit } from "../useStandards";

/** The jobs list answers with these job ids for each status asked; any other status lists none. */
function lists(byStatus: Record<string, string[]>) {
  mockFetchJobs.mockImplementation(async ({ status }: { status: string }) => {
    const jobs = (byStatus[status] ?? []).map((job_id) => ({ job_id, status }));
    return { jobs, total: jobs.length, offset: 0, limit: 200 };
  });
}

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  return { client, invalidate, wrapper };
}

beforeEach(() => {
  mockFetchJobs.mockReset();
  mockFetchJob.mockReset();
  mockStartAudit.mockReset();
});
afterEach(() => vi.useRealTimers());

describe("useAuditJobStatuses", () => {
  it("reads the unfinished audits from the jobs list, one request per unfinished status, and no job by id", async () => {
    lists({ pending: ["job-c", "job-other"], running: ["job-a"], paused: ["job-d"] });
    const { wrapper } = setup();
    const { result } = renderHook(() => useAuditJobStatuses("acme", ["job-a", "job-b", "job-c", "job-d"]), { wrapper });
    await waitFor(() => expect(result.current.get("job-d")).toBe("paused"));
    expect(result.current.get("job-a")).toBe("running");
    expect(result.current.get("job-c")).toBe("pending");
    // Finished, or gone: not in the map.
    expect(result.current.has("job-b")).toBe(false);
    // Another repository's job is not this view's.
    expect(result.current.has("job-other")).toBe(false);
    expect(mockFetchJobs.mock.calls.map((c) => c[0])).toEqual([
      { status: "pending", limit: 200 },
      { status: "running", limit: 200 },
      { status: "awaiting_input", limit: 200 },
      { status: "paused", limit: 200 },
    ]);
    expect(mockFetchJob).not.toHaveBeenCalled();
  });

  it("asks nothing when no repository has an audit job", () => {
    lists({});
    const { wrapper } = setup();
    const { result } = renderHook(() => useAuditJobStatuses("acme", []), { wrapper });
    expect(result.current.size).toBe(0);
    expect(mockFetchJobs).not.toHaveBeenCalled();
  });

  it("polls while one of these audits is unfinished, and stops once none is", async () => {
    // Only the intervals, and moving with real time, so waitFor still polls.
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"], shouldAdvanceTime: true });
    lists({ running: ["job-a"] });
    const { wrapper } = setup();
    const { result } = renderHook(() => useAuditJobStatuses("acme", ["job-a"]), { wrapper });
    await waitFor(() => expect(result.current.get("job-a")).toBe("running"));
    expect(mockFetchJobs).toHaveBeenCalledTimes(4);

    lists({});
    await act(() => vi.advanceTimersByTimeAsync(AUDIT_POLL_MS));
    await waitFor(() => expect(result.current.has("job-a")).toBe(false));
    expect(mockFetchJobs).toHaveBeenCalledTimes(8);

    await act(() => vi.advanceTimersByTimeAsync(AUDIT_POLL_MS * 3));
    expect(mockFetchJobs).toHaveBeenCalledTimes(8);
  });

  it("does not poll for another repository's unfinished job", async () => {
    // Only the intervals, and moving with real time, so waitFor still polls.
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"], shouldAdvanceTime: true });
    lists({ running: ["job-other"] });
    const { wrapper } = setup();
    renderHook(() => useAuditJobStatuses("acme", ["job-a"]), { wrapper });
    await waitFor(() => expect(mockFetchJobs).toHaveBeenCalledTimes(4));
    await act(() => vi.advanceTimersByTimeAsync(AUDIT_POLL_MS * 3));
    expect(mockFetchJobs).toHaveBeenCalledTimes(4);
  });

  it("reads the owner's scores again when an audit it saw unfinished finishes, and not before", async () => {
    lists({ running: ["job-a"] });
    const { client, invalidate, wrapper } = setup();
    const { result } = renderHook(() => useAuditJobStatuses("acme", ["job-a"]), { wrapper });
    await waitFor(() => expect(result.current.get("job-a")).toBe("running"));
    expect(invalidate).not.toHaveBeenCalled();

    lists({});
    await client.refetchQueries({ queryKey: UNFINISHED_JOBS_KEY });
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ["standards", "acme"] }));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["repos", "detail"] });
  });

  it("reads nothing again for an audit that had already finished when first seen", async () => {
    lists({});
    const { invalidate, wrapper } = setup();
    renderHook(() => useAuditJobStatuses("acme", ["job-a"]), { wrapper });
    await waitFor(() => expect(mockFetchJobs).toHaveBeenCalledTimes(4));
    expect(invalidate).not.toHaveBeenCalled();
  });
});

describe("useStartAudit", () => {
  it("reads the states and the unfinished jobs again once an audit starts, so its Audit button turns off", async () => {
    mockStartAudit.mockResolvedValue({ job_id: "job-new" });
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(() => useStartAudit("acme", "design"), { wrapper });
    act(() => result.current.mutate({ repo: "acme/widgets" }));
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: UNFINISHED_JOBS_KEY }));
    expect(mockStartAudit).toHaveBeenCalledWith("acme", "design", "acme/widgets", undefined);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["standards", "acme"] });
  });
});
