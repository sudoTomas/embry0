import { describe, expect, it, beforeEach, afterEach, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { HeartbeatBadge } from "../HeartbeatBadge";

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-07-08T12:00:00Z"));
});

afterEach(() => {
  vi.useRealTimers();
});

describe("HeartbeatBadge", () => {
  it('shows "Updated Ns ago" while polls are fresh', () => {
    render(<HeartbeatBadge lastUpdatedAt={Date.now() - 3_000} />);
    expect(screen.getByTestId("heartbeat-badge")).toHaveTextContent("Updated 3s ago");
  });

  it("flips to STALE after missed polls, ticking without re-render from the parent", () => {
    render(<HeartbeatBadge lastUpdatedAt={Date.now()} />);
    expect(screen.getByTestId("heartbeat-badge")).toHaveTextContent("Updated 0s ago");

    act(() => {
      vi.advanceTimersByTime(20_000);
    });

    expect(screen.getByTestId("heartbeat-badge")).toHaveTextContent("STALE");
  });

  it("flips to OFFLINE once the poll has been gone long enough", () => {
    render(<HeartbeatBadge lastUpdatedAt={Date.now()} />);

    act(() => {
      vi.advanceTimersByTime(50_000);
    });

    expect(screen.getByTestId("heartbeat-badge")).toHaveTextContent("OFFLINE");
  });

  it("shows OFFLINE before the first successful poll", () => {
    render(<HeartbeatBadge lastUpdatedAt={null} />);
    expect(screen.getByTestId("heartbeat-badge")).toHaveTextContent("OFFLINE");
  });
});
