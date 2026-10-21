import { afterEach, describe, expect, it, vi } from "vitest";
import { act, render, screen } from "@testing-library/react";
import { TWEEN_MS } from "@/hooks/useTweenedNumber";
import { SingleSourceTile } from "../SingleSourceTile";

afterEach(() => {
  vi.unstubAllGlobals();
  delete (HTMLElement.prototype as Partial<HTMLElement>).animate;
});

describe("SingleSourceTile", () => {
  it("renders the value when the query is ready", () => {
    render(
      <SingleSourceTile
        label="Running"
        query={{ isError: false, isPending: false, data: 7 }}
        format={(n) => String(n)}
      />,
    );
    expect(screen.getByText("Running")).toBeInTheDocument();
    expect(screen.getByText("7")).toBeInTheDocument();
  });

  it("renders a loading placeholder while the query is pending", () => {
    render(
      <SingleSourceTile
        label="Queued"
        query={{ isError: false, isPending: true, data: undefined }}
        format={(n) => String(n)}
      />,
    );
    const root = screen.getByTestId("tile-Queued");
    expect(root).toHaveAttribute("data-state", "loading");
  });

  it("renders an error placeholder when the query is in an error state", () => {
    render(
      <SingleSourceTile
        label="Failed"
        query={{ isError: true, isPending: false, data: undefined }}
        format={(n) => String(n)}
      />,
    );
    const root = screen.getByTestId("tile-Failed");
    expect(root).toHaveAttribute("data-state", "error");
  });

  // Guards the assay finding: error must win over loading. React-query can
  // briefly report both isError=true and isPending=true while retrying, and
  // an initial 4xx leaves data undefined — the old order rendered '…' instead
  // of surfacing the failure.
  it("prefers error over loading when both flags are set", () => {
    render(
      <SingleSourceTile
        label="Job Success Rate"
        query={{ isError: true, isPending: true, data: undefined }}
        format={(n) => `${n}%`}
      />,
    );
    const root = screen.getByTestId("tile-Job Success Rate");
    expect(root).toHaveAttribute("data-state", "error");
  });

  it("prefers error over a stale successful value (refetch-after-failure)", () => {
    render(
      <SingleSourceTile
        label="Cost Today"
        query={{ isError: true, isPending: false, data: 12.5 }}
        format={(n) => `$${n.toFixed(2)}`}
      />,
    );
    const root = screen.getByTestId("tile-Cost Today");
    expect(root).toHaveAttribute("data-state", "error");
  });

  it("draws a ready number final at once where motion is unknown (jsdom)", () => {
    render(
      <SingleSourceTile
        label="Job success"
        query={{ isError: false, isPending: false, data: 0.874 }}
        format={(n) => `${Math.round(n * 100)}%`}
        turn={2}
      />,
    );
    expect(screen.getByText("87%")).toBeInTheDocument();
    expect(screen.getByTestId("tile-Job success")).toHaveClass("motion-enter");
  });

  it("counts a number up as it loads, then ticks it once when the poll changes it", () => {
    vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
    let queued: FrameRequestCallback[] = [];
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => queued.push(cb));
    vi.stubGlobal("cancelAnimationFrame", () => {});
    const frame = (now: number) => {
      const due = queued;
      queued = [];
      act(() => due.forEach((cb) => cb(now)));
    };
    const animate = vi.fn();
    HTMLElement.prototype.animate = animate;
    const tile = (data: number) => (
      <SingleSourceTile label="Running" query={{ isError: false, isPending: false, data }} format={(n) => String(Math.round(n))} />
    );

    const { rerender } = render(tile(7));
    expect(screen.getByText("0")).toBeInTheDocument();
    frame(0);
    frame(TWEEN_MS);
    expect(screen.getByText("7")).toBeInTheDocument();
    expect(animate).not.toHaveBeenCalled();

    rerender(tile(9));
    expect(animate).toHaveBeenCalledTimes(1);
    expect(animate.mock.contexts[0]).toBe(screen.getByText("7"));
    frame(1000);
    frame(1000 + TWEEN_MS);
    expect(screen.getByText("9")).toBeInTheDocument();
  });
});
