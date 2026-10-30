// Filling in the form stores a test setup and may seed the work machine. The
// repo page reads both through queries keyed by the full repo path, so the
// bootstrap must refresh exactly those keys, or the page keeps showing
// "Testing is not set up yet" after a successful save.
import { describe, expect, it, vi, afterEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useBootstrapRepo } from "@/hooks/useBootstrap";
import { useQaConfig } from "@/hooks/useQaConfig";
import { useRepoPreferences } from "@/hooks/useRepoPreferences";

function appClient() {
  return new QueryClient({
    defaultOptions: { queries: { staleTime: 5 * 60 * 1000, retry: false, refetchOnWindowFocus: false } },
  });
}

function wrapperFor(qc: QueryClient) {
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

afterEach(() => vi.restoreAllMocks());

describe("useBootstrapRepo", () => {
  it("refetches the test setup and preferences of the repository it set up", async () => {
    const qc = appClient();
    const wrapper = wrapperFor(qc);
    const gets: string[] = [];
    vi.spyOn(api, "get").mockImplementation(async (url: string) => {
      gets.push(url);
      return { data: url.endsWith("/qa-config") ? "version: 2\n" : null } as never;
    });
    vi.spyOn(api, "post").mockResolvedValue({
      data: {
        repo: "acme/group/widgets",
        default_branch: "main",
        private: false,
        qa_config_path: "db",
        qa_required: "auto",
        app: "web",
        sandbox_profile: null,
        steps: [],
      },
    } as never);

    const config = renderHook(() => useQaConfig("acme/group/widgets"), { wrapper });
    const prefs = renderHook(() => useRepoPreferences("acme/group/widgets"), { wrapper });
    await waitFor(() => expect(config.result.current.isSuccess).toBe(true));
    await waitFor(() => expect(prefs.result.current.isSuccess).toBe(true));
    const count = (path: string) => gets.filter((url) => url === path).length;
    expect(count("/repos/acme/group/widgets/-/qa-config")).toBe(1);
    expect(count("/repos/acme/group/widgets/-/preferences")).toBe(1);

    const bootstrap = renderHook(() => useBootstrapRepo(), { wrapper });
    await act(async () => {
      await bootstrap.result.current.mutateAsync({ repo: "acme/group/widgets", app: "web" });
    });

    await waitFor(() => expect(count("/repos/acme/group/widgets/-/qa-config")).toBe(2));
    await waitFor(() => expect(count("/repos/acme/group/widgets/-/preferences")).toBe(2));
  });
});
