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
