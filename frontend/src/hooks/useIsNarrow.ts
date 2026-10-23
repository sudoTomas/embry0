import { useSyncExternalStore } from "react";

/** Below this width the sidebar is a drawer over the content, not a column
 * beside it. Matches Tailwind's `md` breakpoint so CSS and JS agree. */
export const NARROW_QUERY = "(max-width: 767px)";

function getQuery(): MediaQueryList | null {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") return null;
  return window.matchMedia(NARROW_QUERY);
}

function subscribe(onChange: () => void): () => void {
  const mql = getQuery();
  if (!mql) return () => {};
  mql.addEventListener("change", onChange);
  return () => mql.removeEventListener("change", onChange);
}

function getSnapshot(): boolean {
  return getQuery()?.matches ?? false;
}

/** True on phone-width viewports. False where matchMedia is missing (jsdom,
 * server), so every existing render keeps the desktop layout. */
export function useIsNarrow(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, () => false);
}
