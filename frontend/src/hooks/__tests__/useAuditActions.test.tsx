import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

const get = vi.fn();
vi.mock("@/api/client", () => ({ api: { get: (...args: unknown[]) => get(...args) } }));

import { AUDIT_ACTIONS_KEY, useAuditActions } from "../useAuditActions";

const REGISTRY = [
  { action: "apikey.repo_refused", label: "API key refused a repository", replaced_by: null },
  { action: "quest.dismissed", label: "Job dismissed", replaced_by: "job.review.dismissed" },
];

function client(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false } } });
}

function wrapperOf(queryClient: QueryClient) {
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

beforeEach(() => {
  get.mockReset();
  get.mockResolvedValue({ data: { actions: REGISTRY } });
});

describe("useAuditActions: the audit action registry from GET /audit/actions (#409)", () => {
  it("reads the registry from its endpoint, with no toast on a failure, as the vocabulary", async () => {
    const { result } = renderHook(() => useAuditActions(), { wrapper: wrapperOf(client()) });
    await waitFor(() =>
      expect(result.current.vocabulary.label("apikey.repo_refused")).toBe("API key refused a repository"),
    );
    expect(result.current.vocabulary.filterOptions).toEqual(["apikey.repo_refused"]);
    expect(get).toHaveBeenCalledWith("/audit/actions", { suppressErrorToast: true });
  });

  it("reads it once and keeps it: a page opened again draws on the kept answer", async () => {
    const queryClient = client();
    const first = renderHook(() => useAuditActions(), { wrapper: wrapperOf(queryClient) });
    await waitFor(() => expect(first.result.current.vocabulary.filterOptions).toHaveLength(1));
    first.unmount();
    const second = renderHook(() => useAuditActions(), { wrapper: wrapperOf(queryClient) });
    expect(second.result.current.vocabulary.label("apikey.repo_refused"), "drawn from the kept answer").toBe(
      "API key refused a repository",
    );
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(get, "the registry changes only with a release: it is not read again").toHaveBeenCalledTimes(1);
  });

  it("keeps the answer with no page open, however long", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const queryClient = client();
      const { result, unmount } = renderHook(() => useAuditActions(), { wrapper: wrapperOf(queryClient) });
      await waitFor(() => expect(result.current.vocabulary.filterOptions).toHaveLength(1));
      unmount();
      await vi.advanceTimersByTimeAsync(60 * 60_000);
      expect(
        queryClient.getQueryData(AUDIT_ACTIONS_KEY),
        "an hour after the last reader closed, the answer is still kept",
      ).toEqual(REGISTRY);
    } finally {
      vi.useRealTimers();
    }
  });

  it("says when the registry cannot be read, and reads every action in sentence case", async () => {
    get.mockRejectedValue(new Error("boom"));
    const { result } = renderHook(() => useAuditActions(), { wrapper: wrapperOf(client()) });
    await waitFor(() => expect(result.current.isError).toBe(true));
    expect(result.current.vocabulary.label("apikey.repo_refused")).toBe("Apikey repo refused");
    expect(result.current.vocabulary.filterOptions).toEqual([]);
  });
});
