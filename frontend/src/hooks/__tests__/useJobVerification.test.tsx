import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

vi.mock("@/api/jobs", () => ({ fetchJobVerification: vi.fn().mockResolvedValue(null) }));
import { fetchJobVerification } from "@/api/jobs";
import { useJobVerification } from "../useJobVerification";

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useJobVerification", () => {
  afterEach(() => { vi.useRealTimers(); vi.mocked(fetchJobVerification).mockClear(); });

  it("fetches once at mount and does not poll before a verification_result has been seen", async () => {
    vi.useFakeTimers();
    renderHook(() => useJobVerification("j1", { live: true, resultSeen: false }), { wrapper });
    await act(async () => { await vi.advanceTimersByTimeAsync(31_000); });
    expect(fetchJobVerification, "a live job without a verify node must not produce a 404 every 15 s").toHaveBeenCalledTimes(1);
  });

  it("polls every 15 s while live once the event has been seen", async () => {
    vi.useFakeTimers();
    renderHook(() => useJobVerification("j1", { live: true, resultSeen: true }), { wrapper });
    await act(async () => { await vi.advanceTimersByTimeAsync(31_000); });
    expect(vi.mocked(fetchJobVerification).mock.calls.length, "the row lands after the event; poll for it").toBeGreaterThanOrEqual(3);
  });

  it("does not poll a finished job", async () => {
    vi.useFakeTimers();
    renderHook(() => useJobVerification("j1", { live: false, resultSeen: true }), { wrapper });
    await act(async () => { await vi.advanceTimersByTimeAsync(31_000); });
    expect(fetchJobVerification).toHaveBeenCalledTimes(1);
  });
});
