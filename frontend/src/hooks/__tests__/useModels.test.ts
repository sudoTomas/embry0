import { describe, it, expect, vi, beforeEach } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useModels } from "../useModels";
import { api } from "@/api/client";

vi.mock("@/api/client", () => ({
  api: { get: vi.fn() },
}));

function wrapper({ children }: { children: ReactNode }) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return createElement(QueryClientProvider, { client: qc }, children);
}

beforeEach(() => {
  vi.mocked(api.get).mockReset();
});

describe("useModels", () => {
  it("folds AVAILABLE provider models into the tier list (RAV-1238)", async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: {
        heavy: "claude-opus-5",
        medium: "claude-sonnet-5",
        light: "claude-haiku-4-5",
        providers: [
          {
            name: "xai",
            base_url: "https://api.x.ai",
            available: true,
            models: [{ model: "grok-4.5", pricing_usd_per_mtok: { input: 2, output: 6 } }],
          },
        ],
      },
    });
    const { result } = renderHook(() => useModels(), { wrapper });
    await waitFor(() =>
      expect(result.current.models).toEqual([
        "claude-opus-5",
        "claude-sonnet-5",
        "claude-haiku-4-5",
        "grok-4.5",
      ]),
    );
  });

  it("hides models from unavailable providers", async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: {
        heavy: "claude-opus-5",
        medium: "claude-sonnet-5",
        light: "claude-haiku-4-5",
        providers: [
          {
            name: "xai",
            base_url: "https://api.x.ai",
            available: false,
            models: [{ model: "grok-4.5", pricing_usd_per_mtok: { input: 2, output: 6 } }],
          },
        ],
      },
    });
    const { result } = renderHook(() => useModels(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.models).not.toContain("grok-4.5");
  });

  it("tolerates a response without a providers field", async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: { heavy: "a", medium: "b", light: "c" },
    });
    const { result } = renderHook(() => useModels(), { wrapper });
    await waitFor(() => expect(result.current.models).toEqual(["a", "b", "c"]));
  });
});

describe("useModels — RAV-1297 enablement", () => {
  it("filters out operator-disabled models", async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: {
        heavy: "claude-opus-5",
        medium: "claude-sonnet-5",
        light: "claude-haiku-4-5",
        providers: [
          {
            name: "xai",
            base_url: "https://api.x.ai",
            available: true,
            models: [
              { model: "grok-4.5", enabled: false, pricing_usd_per_mtok: { input: 2, output: 6 } },
            ],
          },
        ],
      },
    });
    const { result } = renderHook(() => useModels(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.models).not.toContain("grok-4.5");
  });

  it("treats a missing enabled flag as enabled (pre-1297 backend)", async () => {
    vi.mocked(api.get).mockResolvedValue({
      data: {
        heavy: "claude-opus-5",
        medium: "claude-sonnet-5",
        light: "claude-haiku-4-5",
        providers: [
          {
            name: "anthropic",
            base_url: "https://api.anthropic.com",
            available: true,
            models: [
              { model: "claude-opus-5", enabled: true, pricing_usd_per_mtok: { input: 5, output: 25 } },
              { model: "grok-legacy", pricing_usd_per_mtok: { input: null, output: null } },
            ],
          },
        ],
      },
    });
    const { result } = renderHook(() => useModels(), { wrapper });
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(result.current.models).toContain("grok-legacy");
  });
});

describe("useModels provider catalog", () => {
  it("returns the providers next to the model list", async () => {
    const xai = {
      name: "xai",
      base_url: "https://api.x.ai",
      available: false,
      model_prefixes: ["grok-"],
      models: [{ model: "grok-4.5", pricing_usd_per_mtok: { input: 2, output: 6 } }],
    };
    vi.mocked(api.get).mockResolvedValue({
      data: { heavy: "claude-opus-5", medium: "claude-sonnet-5", light: "claude-haiku-4-5", providers: [xai] },
    });
    const { result } = renderHook(() => useModels(), { wrapper });
    await waitFor(() => expect(result.current.providers).toEqual([xai]));
  });

  it("returns no providers before the catalog loads", () => {
    vi.mocked(api.get).mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useModels(), { wrapper });
    expect(result.current.providers).toEqual([]);
  });
});
