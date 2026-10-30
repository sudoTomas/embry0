import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import { act, render, screen } from "@testing-library/react";
import { RepoSelector } from "@/components/issues/RepoSelector";
import { LabelInput } from "@/components/issues/LabelInput";
import { TagInput } from "@/components/ui/TagInput";

/**
 * Each of these inputs closes its suggestions 150 ms after it loses focus,
 * so a click on a suggestion lands first. A dialog that closes inside those
 * 150 ms must take that timer with it: one left running fires after Vitest
 * tears jsdom down, and the state update in it throws "window is not
 * defined" with no test to blame (#256).
 */
const INPUTS: [string, () => ReactElement, () => HTMLElement][] = [
  [
    "RepoSelector",
    () => <RepoSelector value="" onChange={vi.fn()} repos={["acme/widgets"]} />,
    () => screen.getByPlaceholderText("Select repository..."),
  ],
  ["LabelInput", () => <LabelInput value={[]} onChange={vi.fn()} suggestions={["bug"]} />, () => screen.getByLabelText("Add a label")],
  ["TagInput", () => <TagInput id="tags" value={[]} onChange={vi.fn()} suggestions={["bug"]} />, () => screen.getByRole("textbox")],
];

/** The one suggestion each input above offers. */
const SUGGESTION = /^(acme\/widgets|bug)$/;

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

describe("an input that closes its suggestions on blur", () => {
  it.each(INPUTS)("%s clears its close timer when it unmounts", (_name, element, input) => {
    // jsdom starts timers of its own on focus (selectionchange), so follow
    // the close timer by its handle rather than count every timer.
    const started = vi.spyOn(globalThis, "setTimeout");
    const cleared = vi.spyOn(globalThis, "clearTimeout");
    const { unmount } = render(element());
    act(() => input().focus());
    act(() => input().blur());
    const close = started.mock.calls.findIndex(([, ms]) => ms === 150);
    expect(close, "leaving the input starts its 150 ms close timer").toBeGreaterThanOrEqual(0);
    const handle = started.mock.results[close].value;
    unmount();
    expect(cleared, "the close timer outlived the input").toHaveBeenCalledWith(handle);
  });

  it.each(INPUTS)("%s still closes its suggestions 150 ms after a blur while mounted", (_name, element, input) => {
    render(element());
    act(() => input().focus());
    expect(screen.getByRole("button", { name: SUGGESTION }), "focus opens the suggestions").toBeInTheDocument();
    act(() => input().blur());
    act(() => vi.advanceTimersByTime(149));
    expect(screen.getByRole("button", { name: SUGGESTION }), "a click on a suggestion still lands").toBeInTheDocument();
    act(() => vi.advanceTimersByTime(1));
    expect(screen.queryByRole("button", { name: SUGGESTION })).toBeNull();
  });
});
