import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

const api = vi.hoisted(() => ({
  fetchOrganizationNeeds: vi.fn(),
  fetchOrganizationWebhook: vi.fn(),
  installOrganizationWebhook: vi.fn(),
}));
vi.mock("@/api/orgs", () => api);

import { useInstallOrganizationWebhook, useOrganizationNeeds, useOrganizationWebhook } from "../useOrgs";

function wrapperFor(qc: QueryClient) {
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={qc}>{children}</QueryClientProvider>;
}

describe("useInstallOrganizationWebhook", () => {
  it("marks the webhook read and the needs list stale once the install settles", async () => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: 5 * 60_000 }, mutations: { retry: false } } });
    const wrapper = wrapperFor(qc);
    api.fetchOrganizationWebhook.mockResolvedValue({ owner: "acme", installed: false });
    api.fetchOrganizationNeeds.mockResolvedValue({ owner: "acme", needs: [{ id: "n-1", kind: "no_webhook" }] });
    api.installOrganizationWebhook.mockResolvedValue({ owner: "acme", outcome: "done" });

    const webhook = renderHook(() => useOrganizationWebhook("acme"), { wrapper });
    const needs = renderHook(() => useOrganizationNeeds("acme"), { wrapper });
    await waitFor(() => expect(webhook.result.current.data).toEqual({ owner: "acme", installed: false }));
    await waitFor(() => expect(needs.result.current.data).toEqual({ owner: "acme", needs: [{ id: "n-1", kind: "no_webhook" }] }));

    api.fetchOrganizationWebhook.mockResolvedValue({ owner: "acme", installed: true });
    api.fetchOrganizationNeeds.mockResolvedValue({ owner: "acme", needs: [] });
    const install = renderHook(() => useInstallOrganizationWebhook("acme"), { wrapper });
    act(() => install.result.current.mutate());
    await waitFor(() => expect(install.result.current.isSuccess).toBe(true));

    // Both reads were well inside staleTime: only an invalidation refetches them.
    await waitFor(() => expect(webhook.result.current.data).toEqual({ owner: "acme", installed: true }));
    await waitFor(() => expect(needs.result.current.data).toEqual({ owner: "acme", needs: [] }), { timeout: 1_000 });
    expect(api.fetchOrganizationNeeds, "the Overview's needs list refetched, since installing the hook settles a need").toHaveBeenCalledTimes(2);
  });
});
