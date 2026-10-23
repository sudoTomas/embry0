import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const api = vi.hoisted(() => ({
  decideMergeApproval: vi.fn(),
  fetchMergeApproval: vi.fn(),
  fetchMergeApprovals: vi.fn(),
  fetchPendingMergeApprovals: vi.fn(),
}));
vi.mock("@/api/mergeApprovals", () => api);

import { useDecideMergeApproval, useMergeApproval, usePendingMergeApprovals } from "../useMergeApprovals";

function wrapperFor(qc: QueryClient) {
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useDecideMergeApproval", () => {
  it("posts the decision and, once settled, marks every merge-approval read stale", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 5 * 60_000 }, mutations: { retry: false } } });
    const wrapper = wrapperFor(qc);
    api.fetchMergeApproval.mockResolvedValue({ id: "ma-1", status: "pending" });
    api.fetchPendingMergeApprovals.mockResolvedValue([{ id: "ma-1", status: "pending" }]);
    api.decideMergeApproval.mockResolvedValue({ outcome: "merged", status: "merged", dry_run: false, pr_url: "", head_sha: "" });

    // The ask's page and the brief bar both hold a fresh read.
    const detail = renderHook(() => useMergeApproval("ma-1"), { wrapper });
    const pending = renderHook(() => usePendingMergeApprovals(), { wrapper });
    await waitFor(() => expect(detail.result.current.data).toEqual({ id: "ma-1", status: "pending" }));
    await waitFor(() => expect(pending.result.current.data).toEqual([{ id: "ma-1", status: "pending" }]));
    expect(api.fetchMergeApproval).toHaveBeenCalledTimes(1);
    expect(api.fetchPendingMergeApprovals).toHaveBeenCalledTimes(1);

    api.fetchMergeApproval.mockResolvedValue({ id: "ma-1", status: "merged" });
    api.fetchPendingMergeApprovals.mockResolvedValue([]);
    const decide = renderHook(() => useDecideMergeApproval(), { wrapper });
    act(() => decide.result.current.mutate({ id: "ma-1", decision: "approve" }));
    await waitFor(() => expect(decide.result.current.isSuccess).toBe(true));
    expect(api.decideMergeApproval).toHaveBeenCalledWith("ma-1", "approve");

    // Both reads refetch although they were well inside staleTime: the decision invalidated the prefix.
    await waitFor(() => expect(detail.result.current.data).toEqual({ id: "ma-1", status: "merged" }));
    await waitFor(() => expect(pending.result.current.data).toEqual([]));
    expect(api.fetchMergeApproval, "the ask's own read refetched").toHaveBeenCalledTimes(2);
    expect(api.fetchPendingMergeApprovals, "the brief bar's count refetched").toHaveBeenCalledTimes(2);
  });
});
