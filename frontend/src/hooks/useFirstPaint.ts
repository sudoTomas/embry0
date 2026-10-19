import { useEffect, useState } from "react";

/** How long a first paint waits on its answers before it goes ahead without
 * them. Healthy sources answer well inside it; one that hangs, rather than
 * fails, must not hold a page for the client's whole 30 s timeout. */
export const FIRST_PAINT_WAIT_MS = 1500;

/** What a react-query result says about the answers it has had. */
export interface QueryAnswers {
  dataUpdatedAt: number;
  errorUpdatedAt: number;
  failureCount: number;
}

/**
 * Whether a query has answered at least once: with data, with its final
 * error, or with a first failed attempt it is now retrying, so a failing
 * source never holds a page through its retries. It reads the stored update
 * times, not isPending: a failed query with no data turns pending again each
 * time it refetches.
 */
export function hasAnswered(query: QueryAnswers): boolean {
  return query.dataUpdatedAt > 0 || query.errorUpdatedAt > 0 || query.failureCount > 0;
}

/**
 * Whether a view that waits on the answers deciding its layout may paint:
 * once `answered` is true, or once FIRST_PAINT_WAIT_MS has passed without it.
 * Painted once, it stays painted: later answers update it in place.
 */
export function useFirstPaint(answered: boolean): boolean {
  const [painted, setPainted] = useState(answered);
  if (answered && !painted) setPainted(true);
  useEffect(() => {
    if (painted) return;
    const timer = setTimeout(() => setPainted(true), FIRST_PAINT_WAIT_MS);
    return () => clearTimeout(timer);
  }, [painted]);
  return painted || answered;
}
