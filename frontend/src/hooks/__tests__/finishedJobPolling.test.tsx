// A finished job's page stops polling: the job, its questions and its QA run
// change no more once the job has reached a finished status.
import { describe, it, expect, vi, afterEach } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useJob } from "@/hooks/useJobs";
import { useJobInputs } from "@/hooks/useInputs";

function wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

async function getsAfter(ms: number, status: string, hook: () => unknown): Promise<number> {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  const get = vi.spyOn(api, "get").mockImplementation(async (url: string) =>
    ({ data: url.endsWith("/inputs") ? [] : { job_id: "job-1", status } }) as never,
  );
  renderHook(hook, { wrapper });
  await waitFor(() => expect(get).toHaveBeenCalled());
  const before = get.mock.calls.length;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
  return get.mock.calls.length - before;
}

describe("polling a finished job", () => {
  it("useJob stops once the job is merged", async () => {
    expect(await getsAfter(35_000, "pr_merged", () => useJob("job-1"))).toBe(0);
  });

  it("useJob keeps polling a running job", async () => {
    expect(await getsAfter(35_000, "running", () => useJob("job-1"))).toBeGreaterThan(0);
  });

  it("useJobInputs stops when told the job is finished", async () => {
    expect(await getsAfter(20_000, "completed", () => useJobInputs("job-1", "completed"))).toBe(0);
  });
});

describe("polling a finished QA run", () => {
  it("useQaRun stops once the run's job is finished", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { useQaRun } = await import("@/hooks/useQaDashboard");
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(["jobs", "job-qa1"], { job_id: "job-qa1", status: "completed" });
    const get = vi.spyOn(api, "get").mockImplementation(async () => ({ data: { job_id: "job-qa1", apps: [] } }) as never);
    renderHook(() => useQaRun("job-qa1"), {
      wrapper: ({ children }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>,
    });
    await waitFor(() => expect(get).toHaveBeenCalled());
    const before = get.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(40_000);
    });
    expect(get.mock.calls.length - before).toBe(0);
  });
});
