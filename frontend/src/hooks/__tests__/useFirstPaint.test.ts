import { afterEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";
import { FIRST_PAINT_WAIT_MS, hasAnswered, useFirstPaint } from "../useFirstPaint";

const NONE = { dataUpdatedAt: 0, errorUpdatedAt: 0, failureCount: 0 };

afterEach(() => {
  vi.useRealTimers();
});

describe("hasAnswered", () => {
  it("counts data, a final error and a first failed attempt as an answer", () => {
    expect(hasAnswered(NONE)).toBe(false);
    expect(hasAnswered({ ...NONE, dataUpdatedAt: 1 })).toBe(true);
    expect(hasAnswered({ ...NONE, errorUpdatedAt: 1 })).toBe(true);
    // Retrying after a first failure: the page does not wait out the retries.
    expect(hasAnswered({ ...NONE, failureCount: 1 })).toBe(true);
  });

  it("still counts a failed query that is refetching, whose failure count restarts", () => {
    expect(hasAnswered({ dataUpdatedAt: 0, errorUpdatedAt: 1, failureCount: 0 })).toBe(true);
  });
});

describe("useFirstPaint", () => {
  it("paints once the answers are in, and stays painted", () => {
    const { result, rerender } = renderHook(({ answered }) => useFirstPaint(answered), {
      initialProps: { answered: false },
    });
    expect(result.current).toBe(false);
    rerender({ answered: true });
    expect(result.current).toBe(true);
    // A later poll that is still out (a new source) does not take it back.
    rerender({ answered: false });
    expect(result.current).toBe(true);
  });

  it("paints without the answers once the wait runs out, so a hung source holds nothing", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useFirstPaint(false));
    act(() => vi.advanceTimersByTime(FIRST_PAINT_WAIT_MS - 1));
    expect(result.current).toBe(false);
    act(() => vi.advanceTimersByTime(1));
    expect(result.current).toBe(true);
  });
});
