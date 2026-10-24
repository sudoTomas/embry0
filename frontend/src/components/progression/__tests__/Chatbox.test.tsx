import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Progression } from "@/api/progression";
import { progression } from "./fixtures";

const mockUseProgression = vi.fn();
vi.mock("@/hooks/useProgression", () => ({ useProgression: () => mockUseProgression() }));
vi.mock("@/hooks/useAgentIdentity", () => ({ useIdentityLookup: () => () => ({ displayName: "Forge" }) }));

import { Chatbox, MESSAGE_MS } from "../Chatbox";
import { SEEN_KEY } from "../chatbox";

function withRecent(recent: Progression["recent"]): { data: Progression } {
  return { data: progression({ recent }) };
}

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  window.localStorage.clear();
});

describe("Chatbox", () => {
  it("shows nothing on a browser's first visit, and marks the history as seen", () => {
    mockUseProgression.mockReturnValue(
      withRecent([{ kind: "level", at: "2026-10-24T10:00:00+00:00", stat: "coding", level: 84, agent_type: "developer" }]),
    );
    render(<Chatbox />);
    expect(screen.queryByTestId("chatbox-message")).toBeNull();
    expect(window.localStorage.getItem(SEEN_KEY)).toBe("2026-10-24T10:00:00+00:00");
  });

  it("shows each new event once, oldest first, one at a time", () => {
    window.localStorage.setItem(SEEN_KEY, "2026-10-24T09:00:00+00:00");
    mockUseProgression.mockReturnValue(
      withRecent([
        { kind: "achievement", at: "2026-10-24T11:00:00+00:00", id: "forged_in_fire", name: "Forged in Fire", tier: "steel" },
        { kind: "level", at: "2026-10-24T10:00:00+00:00", stat: "coding", level: 84, agent_type: "developer" },
      ]),
    );
    const view = render(<Chatbox />);
    expect(screen.getByTestId("chatbox-message")).toHaveTextContent("Congratulations, Forge just advanced a Coding level. Its Coding level is now 84.");
    expect(screen.getByRole("status")).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(MESSAGE_MS));
    expect(screen.getByTestId("chatbox-message")).toHaveTextContent("You have reached Steel in Forged in Fire.");
    act(() => vi.advanceTimersByTime(MESSAGE_MS));
    expect(screen.queryByTestId("chatbox-message")).toBeNull();
    expect(window.localStorage.getItem(SEEN_KEY)).toBe("2026-10-24T11:00:00+00:00");
    view.rerender(<Chatbox />);
    expect(screen.queryByTestId("chatbox-message")).toBeNull();
  });
});
