import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

import * as apiModule from "@/api/qaDashboard";
import {
  useQaAppHistory,
  useQaRepos,
  useQaRun,
  useQaRunApp,
  useQaRunsForRepo,
} from "../useQaDashboard";

function makeWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

describe("qa-dashboard hooks", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("useQaRepos calls fetchQaRepos with the supplied limit", async () => {
    const spy = vi
      .spyOn(apiModule, "fetchQaRepos")
      .mockResolvedValue([]);
    const { result } = renderHook(() => useQaRepos(25), { wrapper: makeWrapper() });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(spy).toHaveBeenCalledWith(25);
  });

  it("useQaRunsForRepo is disabled when repo is undefined", async () => {
    const spy = vi
      .spyOn(apiModule, "fetchQaRunsForRepo")
      .mockResolvedValue([]);
    const { result } = renderHook(() => useQaRunsForRepo(undefined), {
      wrapper: makeWrapper(),
    });
    expect(result.current.fetchStatus).toBe("idle");
    expect(spy).not.toHaveBeenCalled();
  });

  it("useQaRunsForRepo fires when repo is provided", async () => {
    const spy = vi
      .spyOn(apiModule, "fetchQaRunsForRepo")
      .mockResolvedValue([]);
    const { result } = renderHook(
      () => useQaRunsForRepo("org/r", { limit: 5, offset: 0 }),
      { wrapper: makeWrapper() },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(spy).toHaveBeenCalledWith("org/r", { limit: 5, offset: 0 });
  });

  it("useQaRun is disabled when runId is undefined", () => {
    const spy = vi.spyOn(apiModule, "fetchQaRun").mockResolvedValue({} as never);
    const { result } = renderHook(() => useQaRun(undefined), {
      wrapper: makeWrapper(),
    });
    expect(result.current.fetchStatus).toBe("idle");
    expect(spy).not.toHaveBeenCalled();
  });

  it("useQaRunApp requires both runId and app", async () => {
    const spy = vi.spyOn(apiModule, "fetchQaRunApp").mockResolvedValue({} as never);
    const { result } = renderHook(() => useQaRunApp("j-1", undefined), {
      wrapper: makeWrapper(),
    });
    expect(result.current.fetchStatus).toBe("idle");
    expect(spy).not.toHaveBeenCalled();
  });

  it("useQaAppHistory honours the limit param", async () => {
    const spy = vi
      .spyOn(apiModule, "fetchQaAppHistory")
      .mockResolvedValue([]);
    const { result } = renderHook(
      () => useQaAppHistory("org/r", "hub", 12),
      { wrapper: makeWrapper() },
    );
    await waitFor(() => expect(result.current.isSuccess).toBe(true));
    expect(spy).toHaveBeenCalledWith("org/r", "hub", 12);
  });
});
