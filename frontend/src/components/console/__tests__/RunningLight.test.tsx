import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { RunningLight } from "../RunningLight";

/** The IntersectionObserver the light made, to say when it is seen. */
class FakeObserver {
  static last: FakeObserver | null = null;
  observe = vi.fn();
  disconnect = vi.fn();
  constructor(private readonly onChange: (entries: { isIntersecting: boolean }[]) => void) {
    FakeObserver.last = this;
  }
  show(isIntersecting: boolean) {
    this.onChange([{ isIntersecting }]);
  }
}

afterEach(() => {
  vi.unstubAllGlobals();
  FakeObserver.last = null;
});

describe("RunningLight", () => {
  it("runs while nothing says the card is off screen", () => {
    render(<RunningLight />);
    expect(screen.getByTestId("running-light")).not.toHaveAttribute("data-paused");
  });

  it("pauses while the card is off screen and runs again when it returns", () => {
    vi.stubGlobal("IntersectionObserver", FakeObserver);
    render(<RunningLight />);
    const light = screen.getByTestId("running-light");
    expect(FakeObserver.last?.observe).toHaveBeenCalledWith(light);

    act(() => FakeObserver.last?.show(false));
    expect(light).toHaveAttribute("data-paused");

    act(() => FakeObserver.last?.show(true));
    expect(light).not.toHaveAttribute("data-paused");
  });

  it("stops watching when the card goes", () => {
    vi.stubGlobal("IntersectionObserver", FakeObserver);
    const { unmount } = render(<RunningLight />);
    unmount();
    expect(FakeObserver.last?.disconnect).toHaveBeenCalled();
  });
});
