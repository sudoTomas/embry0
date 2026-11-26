// settle: a wait before a check that something did NOT happen, which reads only once React has rendered what the
// code did, however late a loaded host ran its timers (#732).
import { describe, expect, it } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { useEffect, useState } from "react";
import { settle, stall } from "../settle";

/** Shows "late" a timer of 0 ms after `answer` resolves, as react-query tells a component of an answer. */
function Late({ answer }: { answer: Promise<void> }) {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    void answer.then(() => {
      timer = setTimeout(() => setShown(true), 0);
    });
    return () => clearTimeout(timer);
  }, [answer]);
  return shown ? <p>late</p> : null;
}

/** Shows "late" after `answer` resolves and then `hops` timers, each `stepMs` long, one after the other. */
function LateChain({ answer, hops, stepMs }: { answer: Promise<void>; hops: number; stepMs: number }) {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    const hop = (left: number) => {
      timer = setTimeout(() => (left > 1 ? hop(left - 1) : setShown(true)), stepMs);
    };
    void answer.then(() => hop(hops));
    return () => clearTimeout(timer);
  }, [answer, hops, stepMs]);
  return shown ? <p>late</p> : null;
}

function answered(): { answer: Promise<void>; release: () => void } {
  let release: () => void = () => {};
  const answer = new Promise<void>((resolve) => (release = resolve));
  return { answer, release };
}

describe("settle", () => {
  it("reads only once what a stalled loop scheduled late has rendered", async () => {
    const { answer, release } = answered();
    render(<Late answer={answer} />);
    release();
    await settle(20, 300);
    expect(screen.queryByText("late"), "the late render had its turn before the check").toBeInTheDocument();
  });

  it.each([
    ["a chain of six timers of 0 ms", 6, 0],
    ["one step of 16 ms", 1, 16],
  ])("gives what came after a stall its own wait: %s", async (_name, hops, stepMs) => {
    const { answer, release } = answered();
    render(<LateChain answer={answer} hops={hops} stepMs={stepMs} />);
    release();
    await settle(20, 300);
    expect(screen.queryByText("late"), "a stall that overran the wait is followed by the wait again").toBeInTheDocument();
  });

  it("is needed: a plain wait reads before it, and an absence check then passes whatever the code did", async () => {
    const { answer, release } = answered();
    render(<Late answer={answer} />);
    release();
    const waited = new Promise<void>((resolve) => setTimeout(resolve, 20));
    stall(300);
    await waited;
    expect(screen.queryByText("late"), "the plain wait resolved before the late timer ran").toBeNull();
    await act(() => new Promise<void>((resolve) => setTimeout(resolve, 20)));
    expect(screen.queryByText("late")).toBeInTheDocument();
  });
});
