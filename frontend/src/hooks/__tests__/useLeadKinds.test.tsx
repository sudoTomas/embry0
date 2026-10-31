import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

const get = vi.fn();
vi.mock("@/api/client", () => ({ api: { get: (...args: unknown[]) => get(...args) } }));

import { AxiosError, type AxiosResponse } from "axios";
import { leadKindsRetryDelay, retryLeadKinds, useLeadKinds } from "../useLeadKinds";

function notFound(): AxiosError {
  return new AxiosError("Not Found", "ERR_BAD_REQUEST", undefined, undefined, { status: 404 } as AxiosResponse);
}

const CRACK = { id: "crack", label: "Crack", plural: "Cracks", about: "a crack the scout found in the code", found_by: "the scout" };

function wrapper() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  get.mockReset();
  get.mockResolvedValue({ data: { kinds: [CRACK] } });
});

describe("useLeadKinds: the lead-kind catalogue from GET /lead-kinds (#166)", () => {
  it("reads the catalogue from its own endpoint, by id, with no toast on a failure", async () => {
    const { result } = renderHook(() => useLeadKinds(), { wrapper: wrapper() });
    await waitFor(() => expect(result.current.get("crack")?.label).toBe("Crack"));
    expect(get).toHaveBeenCalledWith("/lead-kinds", { suppressErrorToast: true });
  });

  it("reads it once and keeps it: every page that names a kind shares the one read", async () => {
    const shared = wrapper();
    const first = renderHook(() => useLeadKinds(), { wrapper: shared });
    await waitFor(() => expect(first.result.current.size).toBe(1));
    const second = renderHook(() => useLeadKinds(), { wrapper: shared });
    expect(second.result.current.get("crack")?.plural, "a second reader is drawn from the kept answer").toBe("Cracks");
    first.rerender();
    expect(get, "the catalogue changes only with a release: it is not read again").toHaveBeenCalledTimes(1);
  });

  it("is empty when the backend does not serve it, so each kind reads as its own id, and does not ask again", async () => {
    get.mockRejectedValue(notFound());
    const { result } = renderHook(() => useLeadKinds(), { wrapper: wrapper() });
    await waitFor(() => expect(get).toHaveBeenCalled());
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(result.current.size).toBe(0);
    expect(get, "a 404 is an older backend: trying again cannot change it").toHaveBeenCalledTimes(1);
  });

  it("tries a failed read again twice, waiting longer each time, before naming kinds by their ids", () => {
    const failure = new AxiosError("Service Unavailable", "ERR_BAD_RESPONSE", undefined, undefined, {
      status: 503,
    } as AxiosResponse);
    expect([0, 1, 2].map((failures) => retryLeadKinds(failures, failure)), "two tries again, then none").toEqual([
      true,
      true,
      false,
    ]);
    expect(retryLeadKinds(0, notFound()), "a 404 is not tried again").toBe(false);
    expect([0, 1].map(leadKindsRetryDelay), "one second, then two").toEqual([1_000, 2_000]);
  });

  it("reads it again after a transient failure", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      get.mockRejectedValueOnce(new Error("network")).mockResolvedValue({ data: { kinds: [CRACK] } });
      const { result } = renderHook(() => useLeadKinds(), { wrapper: wrapper() });
      await vi.advanceTimersByTimeAsync(1_500);
      await waitFor(() => expect(result.current.get("crack")?.label).toBe("Crack"));
      expect(get).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });
});
