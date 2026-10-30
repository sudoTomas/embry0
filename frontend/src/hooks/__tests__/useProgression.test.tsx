import { beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { UiSettingsContext } from "@/hooks/useUiSettings";
import type { UiSettings } from "@/lib/uiSettings";

const mockFetchProgression = vi.fn();
vi.mock("@/api/progression", () => ({ fetchProgression: () => mockFetchProgression() }));

import { useProgression } from "../useProgression";

const ON: UiSettings = { game: true, home: "guild" };
const OFF: UiSettings = { game: false, home: "guild" };

function harness(initial: UiSettings) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const ui = { current: initial };
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <UiSettingsContext.Provider value={ui.current}>{children}</UiSettingsContext.Provider>
    </QueryClientProvider>
  );
  const hook = renderHook(() => useProgression(), { wrapper });
  return { ...hook, ui, client };
}

beforeEach(() => {
  mockFetchProgression.mockReset();
  mockFetchProgression.mockResolvedValue({ characters: [], computed_at: "2026-10-30T00:00:00Z" });
});

describe("useProgression and the game layer (#177)", () => {
  it("reads progression while the game is on", async () => {
    const { result } = harness(ON);
    await waitFor(() => expect(result.current.data).toBeDefined());
    expect(mockFetchProgression).toHaveBeenCalledTimes(1);
  });

  it("reads nothing while the game is off, so no page shows a level or an outfit", async () => {
    const { result } = harness(OFF);
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(mockFetchProgression, "no /progression read with the game off").not.toHaveBeenCalled();
    expect(result.current.data).toBeUndefined();
  });

  it("forgets what a page had read once the game goes off", async () => {
    const { result, rerender, ui } = harness(ON);
    await waitFor(() => expect(result.current.data).toBeDefined());
    ui.current = OFF;
    rerender();
    expect(result.current.data, "a level read before the game went off must not stay on the page").toBeUndefined();
    expect(mockFetchProgression).toHaveBeenCalledTimes(1);
  });
});
