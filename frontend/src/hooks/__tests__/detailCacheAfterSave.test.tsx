// An edit form reopened after a save must show what the server now holds.
// The QueryClient uses App.tsx's defaults (staleTime 5 min), so a detail
// query that a mutation forgets to invalidate keeps serving the old values,
// and saving the reopened form would write them back.
import { describe, expect, it, vi, afterEach } from "vitest";
import { renderHook, waitFor, act } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { api } from "@/api/client";
import { useAgent, useUpdateAgent, useResetAgent } from "@/hooks/useAgents";
import { useSandboxProfile, useUpdateSandboxProfile } from "@/hooks/useSandboxProfiles";
import { useTemplate, useRenameTemplate } from "@/hooks/usePipelines";
import type { SandboxProfile } from "@/api/sandbox-profiles";

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

describe("edit forms reopened after a save", () => {
  it("an agent form shows the model saved by PUT", async () => {
    const qc = appClient();
    const wrapper = wrapperFor(qc);
    let model = "model-A";
    vi.spyOn(api, "get").mockImplementation(async () => ({ data: { type: "developer", model } }) as never);
    vi.spyOn(api, "put").mockImplementation(async () => {
      model = "model-B";
      return { data: { type: "developer", model } } as never;
    });

    const first = renderHook(() => useAgent("developer"), { wrapper });
    await waitFor(() => expect(first.result.current.data?.model).toBe("model-A"));
    first.unmount();

    const save = renderHook(() => useUpdateAgent(), { wrapper });
    await act(async () => {
      await save.result.current.mutateAsync({ type: "developer", model: "model-B" });
    });

    const reopened = renderHook(() => useAgent("developer"), { wrapper });
    await waitFor(() => expect(reopened.result.current.data?.model).toBe("model-B"));
  });

  it("an agent form shows the defaults after Reset", async () => {
    const qc = appClient();
    const wrapper = wrapperFor(qc);
    let model = "custom";
    vi.spyOn(api, "get").mockImplementation(async () => ({ data: { type: "developer", model } }) as never);
    vi.spyOn(api, "post").mockImplementation(async () => {
      model = "default";
      return { data: { type: "developer", model } } as never;
    });

    const first = renderHook(() => useAgent("developer"), { wrapper });
    await waitFor(() => expect(first.result.current.data?.model).toBe("custom"));
    first.unmount();

    const reset = renderHook(() => useResetAgent(), { wrapper });
    await act(async () => {
      await reset.result.current.mutateAsync("developer");
    });

    const reopened = renderHook(() => useAgent("developer"), { wrapper });
    await waitFor(() => expect(reopened.result.current.data?.model).toBe("default"));
  });

  it("a sandbox profile form shows the memory saved by PUT", async () => {
    const qc = appClient();
    const wrapper = wrapperFor(qc);
    let memory = "8g";
    vi.spyOn(api, "get").mockImplementation(async () => ({ data: { name: "default", memory } }) as never);
    vi.spyOn(api, "put").mockImplementation(async () => {
      memory = "16g";
      return { data: { name: "default", memory } } as never;
    });

    const first = renderHook(() => useSandboxProfile("default"), { wrapper });
    await waitFor(() => expect(first.result.current.data?.memory).toBe("8g"));
    first.unmount();

    const save = renderHook(() => useUpdateSandboxProfile(), { wrapper });
    await act(async () => {
      await save.result.current.mutateAsync({ name: "default", memory: "16g" } as SandboxProfile);
    });

    const reopened = renderHook(() => useSandboxProfile("default"), { wrapper });
    await waitFor(() => expect(reopened.result.current.data?.memory).toBe("16g"));
  });

  it("a pipeline template reopened after a rename shows the new name", async () => {
    const qc = appClient();
    const wrapper = wrapperFor(qc);
    let name = "Quick fix";
    vi.spyOn(api, "get").mockImplementation(async () => ({ data: { id: "tpl-1", name } }) as never);
    vi.spyOn(api, "put").mockImplementation(async () => {
      name = "Quick fix v2";
      return { data: { id: "tpl-1", name } } as never;
    });
    vi.spyOn(api, "patch").mockImplementation(async () => {
      name = "Quick fix v2";
      return { data: { id: "tpl-1", name } } as never;
    });

    const first = renderHook(() => useTemplate("tpl-1"), { wrapper });
    await waitFor(() => expect(first.result.current.data?.name).toBe("Quick fix"));
    first.unmount();

    const rename = renderHook(() => useRenameTemplate(), { wrapper });
    await act(async () => {
      await rename.result.current.mutateAsync({ templateId: "tpl-1", name: "Quick fix v2" });
    });

    const reopened = renderHook(() => useTemplate("tpl-1"), { wrapper });
    await waitFor(() => expect(reopened.result.current.data?.name).toBe("Quick fix v2"));
  });
});
