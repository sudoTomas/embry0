import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { JobHud } from "../JobHud";

const base = { status: "running", startedAt: "2026-10-20T12:00:00Z", finishedAt: "2026-10-20T12:02:05Z", costUsd: 1.25, turns: 7, tokensIn: 12000, tokensOut: 3400 };

describe("JobHud", () => {
  afterEach(() => { vi.useRealTimers(); });

  it("shows every reading in the pixel HUD", () => {
    render(<JobHud {...base} budgetUsd={null} />);
    const hud = screen.getByTestId("job-hud");
    expect(hud.className.split(" ")).toContain("hud");
    expect(hud.querySelector("[data-hud='status']")?.textContent).toBe("running");
    expect(hud.querySelector("[data-hud='elapsed']")?.textContent).toBe("2m 5s");
    expect(hud.querySelector("[data-hud='cost']")?.textContent).toBe("$1.25");
    expect(hud.querySelector("[data-hud='turns']")?.textContent).toBe("7");
    expect(hud.querySelector("[data-hud='tokens-in']")?.textContent).toBe("12,000");
    expect(hud.querySelector("[data-hud='tokens-out']")?.textContent).toBe("3,400");
  });

  it("owns the elapsed ticker: it ticks while the job runs without a new prop", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-10-20T12:00:30Z"));
    render(<JobHud {...base} finishedAt={null} budgetUsd={null} />);
    const elapsed = () => screen.getByTestId("job-hud").querySelector("[data-hud='elapsed']")?.textContent;
    expect(elapsed()).toBe("30s");
    act(() => { vi.advanceTimersByTime(2000); });
    expect(elapsed(), "the HUD must tick on its own, so the page does not re-render every second").toBe("32s");
  });

  it("shows a dash before the job starts", () => {
    render(<JobHud {...base} startedAt={null} finishedAt={null} budgetUsd={null} />);
    expect(screen.getByTestId("job-hud").querySelector("[data-hud='elapsed']")?.textContent).toBe("—");
  });

  it("no budget, no meter", () => {
    const { container } = render(<JobHud {...base} budgetUsd={null} />);
    expect(container.querySelector("[role='meter']"), "a job without a budget must show no meter").toBeNull();
    expect(container.textContent).not.toContain("NaN");
    const zero = render(<JobHud {...base} budgetUsd={0} />);
    expect(zero.container.querySelector("[role='meter']")).toBeNull();
  });

  it("the meter turns amber at 80 percent and red at 100", () => {
    const level = (cost: number) => {
      const { container, unmount } = render(<JobHud {...base} costUsd={cost} budgetUsd={10} />);
      const meter = container.querySelector("[role='meter']")!;
      const out = { level: meter.getAttribute("data-level"), now: meter.getAttribute("aria-valuenow"), max: meter.getAttribute("aria-valuemax") };
      unmount();
      return out;
    };
    expect(level(7.99)).toEqual({ level: "ok", now: "7.99", max: "10" });
    expect(level(8)).toEqual({ level: "warn", now: "8", max: "10" });
    expect(level(10)).toEqual({ level: "over", now: "10", max: "10" });
    expect(level(12.5).level, "over budget stays 'over', the bar is capped at full").toBe("over");
  });
});
