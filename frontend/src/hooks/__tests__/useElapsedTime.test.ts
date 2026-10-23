import { describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { useElapsedTime } from "../useElapsedTime";

describe("useElapsedTime", () => {
  it("formats a finished span and stays put", () => {
    const { result } = renderHook(() => useElapsedTime("2026-10-20T12:00:00Z", "2026-10-20T12:03:12Z"));
    expect(result.current).toBe("3m 12s");
  });

  it("ticks while unfinished", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-20T12:00:30Z"));
    const { result } = renderHook(() => useElapsedTime("2026-10-20T12:00:00Z", null));
    expect(result.current).toBe("30s");
    act(() => { vi.advanceTimersByTime(2000); });
    expect(result.current, "the ticker must advance once a second").toBe("32s");
    vi.useRealTimers();
  });

  it("is empty before the job starts", () => {
    const { result } = renderHook(() => useElapsedTime(null, null));
    expect(result.current).toBe("");
  });
});
