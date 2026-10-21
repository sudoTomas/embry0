import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { createGlide } from "../useGlide";
import { EASE_TRAVEL } from "@/lib/motion";

/** A card element whose place the test moves by hand. */
function fakeCard(left: number, top: number) {
  const place = { left, top };
  const el = {
    style: document.createElement("div").style,
    getBoundingClientRect: () => ({ left: place.left, top: place.top }) as DOMRect,
    animate: vi.fn(() => ({ cancel: vi.fn() }) as unknown as Animation),
  };
  return {
    el: el as unknown as HTMLElement,
    animate: el.animate,
    moveTo(l: number, t: number) {
      place.left = l;
      place.top = t;
    },
  };
}

function stubReducedMotion(reduce: boolean) {
  vi.stubGlobal(
    "matchMedia",
    vi.fn(() => ({ matches: reduce, addEventListener: () => {}, removeEventListener: () => {} })),
  );
}

beforeEach(() => stubReducedMotion(false));
afterEach(() => vi.unstubAllGlobals());

describe("createGlide", () => {
  it("glides a card that remounted in another lane from its old place", () => {
    const glide = createGlide();
    const queued = fakeCard(300, 40);
    const detachQueued = glide.attach("job-1", queued.el);
    glide.play();

    // A poll moves the job: the old card goes, a new one mounts elsewhere.
    glide.snapshot();
    detachQueued();
    const running = fakeCard(600, 40);
    glide.attach("job-1", running.el);
    glide.play();

    expect(running.animate).toHaveBeenCalledTimes(1);
    expect(running.animate).toHaveBeenCalledWith(
      [{ transform: "translate(-300px, 0px)" }, { transform: "none" }],
      { duration: 480, easing: EASE_TRAVEL },
    );
  });

  it("glides a card that stayed mounted but was pushed along its lane", () => {
    const glide = createGlide();
    const card = fakeCard(0, 0);
    glide.attach("job-1", card.el);
    glide.play();

    glide.snapshot();
    card.moveTo(0, 120);
    glide.play();

    expect(card.animate).toHaveBeenCalledWith(
      [{ transform: "translate(0px, -120px)" }, { transform: "none" }],
      expect.any(Object),
    );
  });

  it("lifts a travelling card over the others until it lands", () => {
    const glide = createGlide();
    const card = fakeCard(0, 0);
    glide.attach("job-1", card.el);
    glide.play();

    glide.snapshot();
    card.moveTo(300, 0);
    glide.play();

    expect(card.el.style.zIndex).toBe("1");
    const flight = card.animate.mock.results[0].value as Animation;
    flight.onfinish?.call(flight, {} as AnimationPlaybackEvent);
    expect(card.el.style.zIndex).toBe("");
  });

  it("keeps a card lifted when a late cancel arrives from the flight it replaced", () => {
    const glide = createGlide();
    const card = fakeCard(0, 0);
    glide.attach("job-1", card.el);
    glide.play();

    glide.snapshot();
    card.moveTo(300, 0);
    glide.play();
    const first = card.animate.mock.results[0].value as Animation;

    glide.snapshot();
    card.moveTo(600, 0);
    glide.play();
    first.oncancel?.call(first, {} as AnimationPlaybackEvent);

    expect(card.el.style.zIndex).toBe("1");
  });

  it("does not glide a card the board had not shown before", () => {
    const glide = createGlide();
    const old = fakeCard(0, 0);
    glide.attach("job-1", old.el);
    glide.play();

    glide.snapshot();
    const fresh = fakeCard(0, 120);
    expect(glide.knows("job-2")).toBe(false);
    glide.attach("job-2", fresh.el);
    glide.play();

    expect(fresh.animate).not.toHaveBeenCalled();
    expect(glide.knows("job-2")).toBe(true);
  });

  it("does not glide on the first paint", () => {
    const glide = createGlide();
    const card = fakeCard(0, 0);
    glide.attach("job-1", card.el);
    glide.play();
    expect(card.animate).not.toHaveBeenCalled();
  });

  it("leaves a card that did not move alone", () => {
    const glide = createGlide();
    const card = fakeCard(10, 10);
    glide.attach("job-1", card.el);
    glide.play();
    glide.snapshot();
    glide.play();
    expect(card.animate).not.toHaveBeenCalled();
  });

  it("does not glide after a reset, the fresh paint of a filter change", () => {
    const glide = createGlide();
    const card = fakeCard(0, 0);
    glide.attach("job-1", card.el);
    glide.play();

    glide.snapshot();
    card.moveTo(300, 0);
    glide.reset();
    glide.play();

    expect(card.animate).not.toHaveBeenCalled();
  });

  it("does not glide for someone who asked for reduced motion", () => {
    stubReducedMotion(true);
    const glide = createGlide();
    const card = fakeCard(0, 0);
    glide.attach("job-1", card.el);
    glide.play();

    glide.snapshot();
    card.moveTo(300, 0);
    glide.play();

    expect(card.animate).not.toHaveBeenCalled();
  });

  it("keeps a card's new element when the old one detaches after it", () => {
    const glide = createGlide();
    const old = fakeCard(0, 0);
    const detachOld = glide.attach("job-1", old.el);
    glide.play();

    glide.snapshot();
    const moved = fakeCard(300, 0);
    glide.attach("job-1", moved.el);
    detachOld();
    glide.play();

    expect(moved.animate).toHaveBeenCalledTimes(1);
  });
});
