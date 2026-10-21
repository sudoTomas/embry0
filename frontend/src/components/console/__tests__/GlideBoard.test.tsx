import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { GlideBoard, GlideItem } from "../GlideBoard";
import { createGlide, type Glide } from "@/hooks/useGlide";

type Lanes = Record<string, string[]>;

/** jsdom lays nothing out: a card's place is its lane (300 px apart) and its
 * turn within the lane (120 px apart). */
function placeByLane(this: HTMLElement): DOMRect {
  const lane = this.closest("[data-lane]");
  const lanes = lane?.parentElement ? Array.from(lane.parentElement.children) : [];
  const siblings = this.parentElement ? Array.from(this.parentElement.children) : [];
  return { left: lanes.indexOf(lane as Element) * 300, top: siblings.indexOf(this) * 120 } as DOMRect;
}

/** The elements the board animated, with the frames each one played. */
let animated: { el: HTMLElement; keyframes: Keyframe[] }[] = [];

function Board({ glide, lanes, paintKey = "" }: { glide: Glide; lanes: Lanes; paintKey?: string }) {
  return (
    <GlideBoard glide={glide} layout={lanes} paintKey={paintKey}>
      <div>
        {Object.entries(lanes).map(([lane, ids]) => (
          <div key={lane} data-lane={lane}>
            {ids.map((id, i) => (
              <GlideItem key={id} id={id} index={i}>
                <span>{id}</span>
              </GlideItem>
            ))}
          </div>
        ))}
      </div>
    </GlideBoard>
  );
}

/** The GlideItem around a card. */
const cardOf = (id: string) => screen.getByText(id).parentElement as HTMLElement;

beforeEach(() => {
  animated = [];
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({ matches: false, addEventListener: () => {}, removeEventListener: () => {} })),
  );
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(placeByLane);
  HTMLElement.prototype.animate = function (this: HTMLElement, keyframes: Keyframe[]) {
    animated.push({ el: this, keyframes });
    return { cancel: () => {} } as unknown as Animation;
  } as HTMLElement["animate"];
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete (HTMLElement.prototype as Partial<HTMLElement>).animate;
});

describe("GlideBoard", () => {
  it("lets every card arrive on the first paint, with nothing gliding", () => {
    render(<Board glide={createGlide()} lanes={{ queued: ["a", "b"], running: [] }} />);

    expect(cardOf("a")).toHaveClass("motion-enter");
    expect(cardOf("b").style.getPropertyValue("--i")).toBe("1");
    expect(animated).toEqual([]);
  });

  it("glides a job's card from its old lane to its new one", () => {
    const glide = createGlide();
    const { rerender } = render(<Board glide={glide} lanes={{ queued: ["a"], running: [] }} />);

    rerender(<Board glide={glide} lanes={{ queued: [], running: ["a"] }} />);

    expect(animated).toHaveLength(1);
    expect(animated[0].el).toBe(cardOf("a"));
    expect(animated[0].keyframes[0]).toEqual({ transform: "translate(-300px, 0px)" });
    // It travelled; it did not arrive a second time.
    expect(cardOf("a")).not.toHaveClass("motion-enter");
  });

  it("lets a new card arrive while the card it pushed down glides", () => {
    const glide = createGlide();
    const { rerender } = render(<Board glide={glide} lanes={{ done: ["a"] }} />);

    rerender(<Board glide={glide} lanes={{ done: ["b", "a"] }} />);

    expect(cardOf("b")).toHaveClass("motion-enter");
    expect(animated.map((x) => x.el)).toEqual([cardOf("a")]);
    expect(animated[0].keyframes[0]).toEqual({ transform: "translate(0px, -120px)" });
  });

  it("paints afresh when the filters change, gliding nothing", () => {
    const glide = createGlide();
    const { rerender } = render(<Board glide={glide} lanes={{ queued: ["a"], running: [] }} />);

    rerender(<Board glide={glide} lanes={{ queued: [], running: ["a"] }} paintKey="running|" />);

    expect(animated).toEqual([]);
  });
});
