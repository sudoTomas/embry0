import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { renderHook } from "@testing-library/react";
import { useComponentTimeout } from "../useComponentTimeout";

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useComponentTimeout", () => {
  it("runs the callback after the delay while the component is mounted", () => {
    const callback = vi.fn();
    const { result } = renderHook(() => useComponentTimeout());
    result.current(callback, 150);
    vi.advanceTimersByTime(149);
    expect(callback).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(callback).toHaveBeenCalledTimes(1);
  });

  it("runs every timer it starts, like setTimeout, not only the last", () => {
    const first = vi.fn();
    const second = vi.fn();
    const { result } = renderHook(() => useComponentTimeout());
    result.current(first, 150);
    result.current(second, 150);
    vi.advanceTimersByTime(150);
    expect([first.mock.calls.length, second.mock.calls.length]).toEqual([1, 1]);
  });

  it("clears its pending timers when the component unmounts", () => {
    const callback = vi.fn();
    const { result, unmount } = renderHook(() => useComponentTimeout());
    result.current(callback, 150);
    result.current(callback, 300);
    expect(vi.getTimerCount(), "both timers are pending while mounted").toBe(2);
    unmount();
    expect(vi.getTimerCount(), "a timer outlived its component").toBe(0);
    vi.advanceTimersByTime(300);
    expect(callback).not.toHaveBeenCalled();
  });

  it("hands back the same function on every render", () => {
    const { result, rerender } = renderHook(() => useComponentTimeout());
    const first = result.current;
    rerender();
    expect(result.current).toBe(first);
  });
});
