import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

const mockFetchJob = vi.fn();
vi.mock("@/api/jobs", () => ({ fetchJob: (...a: unknown[]) => mockFetchJob(...a) }));

import { useAuditJobStatuses } from "../useStandards";

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  return { client, invalidate, wrapper };
}

beforeEach(() => mockFetchJob.mockReset());

describe("useAuditJobStatuses", () => {
  it("reads each audit job's status by id", async () => {
    mockFetchJob.mockImplementation(async (id: string) => ({ job_id: id, status: id === "job-a" ? "running" : "failed" }));
    const { wrapper } = setup();
    const { result } = renderHook(() => useAuditJobStatuses("acme", ["job-a", "job-b"]), { wrapper });
    await waitFor(() => expect(result.current.get("job-b")).toBe("failed"));
    expect(result.current.get("job-a")).toBe("running");
  });

  it("reads the owner's scores again when an audit it saw running finishes, and not before", async () => {
    mockFetchJob.mockResolvedValueOnce({ job_id: "job-a", status: "running" });
    const { client, invalidate, wrapper } = setup();
    const { result } = renderHook(() => useAuditJobStatuses("acme", ["job-a"]), { wrapper });
    await waitFor(() => expect(result.current.get("job-a")).toBe("running"));
    expect(invalidate).not.toHaveBeenCalled();

    mockFetchJob.mockResolvedValueOnce({ job_id: "job-a", status: "completed" });
    await client.refetchQueries({ queryKey: ["jobs", "job-a"] });
    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: ["standards", "acme"] }));
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["repos", "detail"] });
  });

  it("reads nothing again for an audit that had already finished when first seen", async () => {
    mockFetchJob.mockResolvedValue({ job_id: "job-a", status: "completed" });
    const { invalidate, wrapper } = setup();
    const { result } = renderHook(() => useAuditJobStatuses("acme", ["job-a"]), { wrapper });
    await waitFor(() => expect(result.current.get("job-a")).toBe("completed"));
    expect(invalidate).not.toHaveBeenCalled();
  });
});
