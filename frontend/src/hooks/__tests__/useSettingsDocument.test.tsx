import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";

const mockFetchSettings = vi.fn();
const mockSaveSettings = vi.fn();
const mockUndo = vi.fn();
const mockFetchRevision = vi.fn();
const mockPreview = vi.fn();
vi.mock("@/api/settings", () => ({
  fetchSettings: (...a: unknown[]) => mockFetchSettings(...a),
  saveSettings: (...a: unknown[]) => mockSaveSettings(...a),
  undoSettingsChange: (...a: unknown[]) => mockUndo(...a),
  fetchSettingsRevision: (...a: unknown[]) => mockFetchRevision(...a),
  fetchEffectiveSettings: vi.fn(),
  fetchSettingsRevisions: vi.fn(),
  previewSettings: (...a: unknown[]) => mockPreview(...a),
}));

import {
  SETTINGS_KEY,
  usePreviewSettings,
  useSaveSettings,
  useSettingsDocument,
  useSettingsRevision,
  useUndoSettingsChange,
} from "../useSettingsDocument";

const DEPLOYMENT_LAYER = { layer: "deployment" } as const;

function setup() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
  return { client, invalidate, wrapper };
}

const status = (code: number) => Object.assign(new Error(`HTTP ${code}`), { response: { status: code, data: {} } });

beforeEach(() => {
  for (const mock of [mockFetchSettings, mockSaveSettings, mockUndo, mockFetchRevision, mockPreview]) mock.mockReset();
});

describe("useSaveSettings", () => {
  it("refreshes every view of the document after a save", async () => {
    mockSaveSettings.mockResolvedValue({ generation: 4, revision_id: 9, changes: [], unknown_sections: [] });
    const { wrapper, invalidate } = setup();
    const { result } = renderHook(() => useSaveSettings(), { wrapper });
    await act(() => result.current.mutateAsync({ yamlText: "version: 1\n", generation: 3 }));
    expect(mockSaveSettings).toHaveBeenCalledWith({ yamlText: "version: 1\n", generation: 3 }, DEPLOYMENT_LAYER);
    expect(invalidate).toHaveBeenCalledWith({ queryKey: SETTINGS_KEY });
  });

  it("reads the new version in on a 409, before the caller's onError runs", async () => {
    mockSaveSettings.mockRejectedValue(status(409));
    mockFetchSettings.mockResolvedValueOnce({ generation: 3 }).mockResolvedValueOnce({ generation: 5 });
    const { wrapper } = setup();
    const { result } = renderHook(() => ({ doc: useSettingsDocument(), save: useSaveSettings() }), { wrapper });
    await waitFor(() => expect(result.current.doc.data).toEqual({ generation: 3 }));

    let seenByCaller: unknown;
    await act(async () => {
      await new Promise<void>((resolve) => {
        result.current.save.mutate(
          { yamlText: "x: 1\n", generation: 3 },
          {
            onError: () => {
              seenByCaller = mockFetchSettings.mock.calls.length;
              resolve();
            },
          },
        );
      });
    });
    expect(seenByCaller, "the document was read again before the caller heard of the conflict").toBe(2);
    await waitFor(() => expect(result.current.doc.data).toEqual({ generation: 5 }));
  });

  it("refreshes nothing on any other failure", async () => {
    mockSaveSettings.mockRejectedValue(status(422));
    const { wrapper, invalidate } = setup();
    const { result } = renderHook(() => useSaveSettings(), { wrapper });
    await act(async () => {
      await result.current.mutateAsync({ yamlText: "x: 1\n", generation: 3 }).catch(() => undefined);
    });
    expect(invalidate).not.toHaveBeenCalled();
  });
});

describe("usePreviewSettings", () => {
  it("sends the text with the generation the edit started from, and refreshes nothing on success", async () => {
    mockPreview.mockResolvedValue({ would_change: true, text_diff: "", changes: [], unknown_sections: [], unknown_keys: [] });
    const { wrapper, invalidate } = setup();
    const { result } = renderHook(() => usePreviewSettings(), { wrapper });
    await act(() => result.current.mutateAsync({ yamlText: "x: 1\n", generation: 3 }));
    expect(mockPreview).toHaveBeenCalledWith("x: 1\n", 3, DEPLOYMENT_LAYER);
    expect(invalidate).not.toHaveBeenCalled();
  });

  it("reads the new version in on a 409 (a stale tag), before the caller's onError runs", async () => {
    mockPreview.mockRejectedValue(status(409));
    mockFetchSettings.mockResolvedValueOnce({ generation: 3 }).mockResolvedValueOnce({ generation: 5 });
    const { wrapper } = setup();
    const { result } = renderHook(() => ({ doc: useSettingsDocument(), preview: usePreviewSettings() }), { wrapper });
    await waitFor(() => expect(result.current.doc.data).toEqual({ generation: 3 }));

    let seenByCaller: unknown;
    await act(async () => {
      await new Promise<void>((resolve) => {
        result.current.preview.mutate(
          { yamlText: "x: 1\n", generation: 3 },
          {
            onError: () => {
              seenByCaller = mockFetchSettings.mock.calls.length;
              resolve();
            },
          },
        );
      });
    });
    expect(seenByCaller, "the document was read again before the caller heard of the conflict").toBe(2);
  });
});

describe("useUndoSettingsChange", () => {
  it("refreshes after an undo, and after a 409 (it was no longer the newest)", async () => {
    mockUndo.mockResolvedValueOnce({ generation: 5, revision_id: 10, changes: [] }).mockRejectedValueOnce(status(409));
    const { wrapper, invalidate } = setup();
    const { result } = renderHook(() => useUndoSettingsChange(), { wrapper });
    await act(() => result.current.mutateAsync(9));
    expect(mockUndo).toHaveBeenCalledWith(9);
    expect(invalidate).toHaveBeenCalledTimes(1);
    await act(async () => {
      await result.current.mutateAsync(8).catch(() => undefined);
    });
    expect(invalidate).toHaveBeenCalledTimes(2);
  });
});

describe("useSettingsRevision", () => {
  it("reads nothing until a revision is opened", async () => {
    mockFetchRevision.mockResolvedValue({ id: 7 });
    const { wrapper } = setup();
    const { result, rerender } = renderHook(({ id }: { id: number | null }) => useSettingsRevision(id), {
      wrapper,
      initialProps: { id: null as number | null },
    });
    expect(mockFetchRevision).not.toHaveBeenCalled();
    rerender({ id: 7 });
    await waitFor(() => expect(result.current.data).toEqual({ id: 7 }));
    expect(mockFetchRevision).toHaveBeenCalledWith(7);
  });
});

describe("layers", () => {
  const acme = { layer: "organization", owner: "acme" } as const;

  it("keeps each layer's document apart, and saves a layer as that layer", async () => {
    mockFetchSettings.mockImplementation(async (layer: { layer: string }) => ({ generation: layer.layer === "deployment" ? 3 : 0 }));
    mockSaveSettings.mockResolvedValue({ generation: 1, revision_id: 4, changes: [], unknown_sections: [] });
    const { wrapper, invalidate } = setup();
    const { result } = renderHook(
      () => ({ deployment: useSettingsDocument(), acme: useSettingsDocument(acme), save: useSaveSettings(acme) }),
      { wrapper },
    );
    await waitFor(() => expect(result.current.acme.data).toEqual({ generation: 0 }));
    expect(result.current.deployment.data, "one layer's document was read as another's").toEqual({ generation: 3 });
    expect(mockFetchSettings).toHaveBeenCalledWith(acme);
    expect(mockFetchSettings).toHaveBeenCalledWith(DEPLOYMENT_LAYER);

    await act(() => result.current.save.mutateAsync({ yamlText: "asks:\n  hours: 08-17\n", generation: 0 }));
    expect(mockSaveSettings).toHaveBeenCalledWith({ yamlText: "asks:\n  hours: 08-17\n", generation: 0 }, acme);
    // A layer's change can change what the layers under it read: every view is read again.
    expect(invalidate).toHaveBeenCalledWith({ queryKey: SETTINGS_KEY });
  });

  it("previews a layer as that layer", async () => {
    mockPreview.mockResolvedValue({ would_change: true, text_diff: "", changes: [], unknown_sections: [], unknown_keys: [] });
    const { wrapper } = setup();
    const { result } = renderHook(() => usePreviewSettings(acme), { wrapper });
    await act(() => result.current.mutateAsync({ yamlText: "x: 1\n", generation: 2 }));
    expect(mockPreview).toHaveBeenCalledWith("x: 1\n", 2, acme);
  });
});
