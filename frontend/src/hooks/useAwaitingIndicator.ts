import { useEffect } from "react";
import { useQueue } from "./useQueue";

/** Matches a previously applied "(N⚠) " title badge so re-applies replace it
 * rather than stacking prefixes. */
const TITLE_BADGE_RE = /^\(\d+⚠\) /;

/**
 * Awaiting-input prominence (live-console spec): counts blocked jobs
 * (awaiting_input + paused, the board's Needs You lane) from the queue poll
 * and mirrors the count into document.title as "(2⚠) …" so a blocked job is
 * loud even from another browser tab. Returns the count for the sidebar
 * Console badge. Mounted once in the Sidebar — always rendered, so the title
 * stays honest on every route.
 */
export function useAwaitingIndicator(): number {
  const { data } = useQueue();
  const count = (data?.awaiting_input ?? 0) + (data?.paused ?? 0);

  useEffect(() => {
    const base = document.title.replace(TITLE_BADGE_RE, "");
    document.title = count > 0 ? `(${count}⚠) ${base}` : base;
    return () => {
      document.title = document.title.replace(TITLE_BADGE_RE, "");
    };
  }, [count]);

  return count;
}
