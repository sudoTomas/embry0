import { createContext, useCallback, useContext, useState, type RefCallback } from "react";
import { prefersReducedMotion } from "@/hooks/useReducedMotion";
import { EASE_TRAVEL } from "@/lib/motion";

/** How long a card takes to travel to its new place. */
const GLIDE_MS = 480;

/** Moves smaller than this (in px) are rounding, not travel. */
const MIN_TRAVEL_PX = 1;

/**
 * Where the board's cards are, by job id (and its lanes' headers, by lane),
 * so a card can glide from its old place to its new one (FLIP: first, last,
 * invert, play). A job that moves lane remounts under another parent, so
 * the card cannot remember its own place: the board keeps it for every card.
 */
export interface Glide {
  /** Body of a card's ref callback. Returns the cleanup for when it unmounts. */
  attach(id: string, el: HTMLElement): () => void;
  /** Whether the job had a card on the board at the last commit. */
  knows(id: string): boolean;
  /** Before a commit that may move cards: read where every card is now. */
  snapshot(): void;
  /** Forget where cards were: the coming commit is a fresh paint. */
  reset(): void;
  /** After the commit: each card that changed place travels from the old one. */
  play(): void;
}

export function createGlide(): Glide {
  const elements = new Map<string, HTMLElement>();
  const inFlight = new WeakMap<HTMLElement, Animation>();
  let before: Map<string, DOMRect> | null = null;
  let seen = new Set<string>();

  return {
    attach(id, el) {
      elements.set(id, el);
      return () => {
        // The card's new element may have attached first; keep that one.
        if (elements.get(id) === el) elements.delete(id);
      };
    },

    knows: (id) => seen.has(id),

    snapshot() {
      before = new Map();
      for (const [id, el] of elements) before.set(id, el.getBoundingClientRect());
    },

    reset() {
      before = null;
    },

    play() {
      const first = before;
      before = null;
      seen = new Set(elements.keys());
      if (!first || first.size === 0 || prefersReducedMotion()) return;

      // A glide still in flight would shift the reads below; the snapshot
      // already holds where that card shows, so it carries on from there.
      for (const el of elements.values()) inFlight.get(el)?.cancel();

      // All reads, then all writes: one layout for the whole board.
      const moves: [HTMLElement, number, number][] = [];
      for (const [id, el] of elements) {
        const from = first.get(id);
        if (!from || typeof el.animate !== "function") continue;
        const to = el.getBoundingClientRect();
        const dx = from.left - to.left;
        const dy = from.top - to.top;
        if (Math.abs(dx) >= MIN_TRAVEL_PX || Math.abs(dy) >= MIN_TRAVEL_PX) moves.push([el, dx, dy]);
      }
      for (const [el, dx, dy] of moves) {
        const flight = el.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: "none" }], {
          duration: GLIDE_MS,
          easing: EASE_TRAVEL,
        });
        // A card on its way passes over the cards it crosses, whichever lane
        // comes first on the page. A cancelled flight reports late, after
        // the one replacing it took off, so only the current one lands it.
        el.style.zIndex = "1";
        const land = () => {
          if (inFlight.get(el) === flight) el.style.removeProperty("z-index");
        };
        flight.onfinish = land;
        flight.oncancel = land;
        inFlight.set(el, flight);
      }
    },
  };
}

/** The board's glide registry, made once for the page that owns the board. */
export function useGlide(): Glide {
  const [glide] = useState(createGlide);
  return glide;
}

/** The registry of the GlideBoard around a card or a lane's header. */
export const GlideContext = createContext<Glide | null>(null);

/**
 * A ref that keeps an element's place on the board under `id`, so it glides
 * when a poll moves it. Outside a GlideBoard it does nothing.
 */
export function useGlideRef(id: string): RefCallback<HTMLElement> {
  const glide = useContext(GlideContext);
  return useCallback((el: HTMLElement) => glide?.attach(id, el), [glide, id]);
}
