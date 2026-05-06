import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";

/**
 * Subscribe to /api/v1/qa/runs/{runId}/events as Server-Sent Events and
 * invalidate the run-detail react-query key on each event so the existing
 * `useQaRun` hook refetches the latest run snapshot. Closes cleanly on
 * `done` events, on transport errors, and on unmount.
 *
 * Auth caveat: the EventSource browser API cannot send `Authorization`
 * headers, so this hook relies on the dashboard being same-origin and
 * either AUTH_DEV_MODE=true or cookie-based auth. In a production-Bearer
 * deploy the connection 401s and the polling fallback in `useQaRun`
 * (15 s) keeps the dashboard live.
 *
 * Reconnect behavior: browsers auto-reconnect SSE on `onerror` by default.
 * The hook closes the connection on the *first* error to prevent runaway
 * reconnects against a 401 endpoint — polling reconciles state regardless,
 * so a single failure flipping to polling is the correct trade-off.
 */
export function useQaRunEvents(runId: string | undefined): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!runId) return;

    const url = `/api/v1/qa/runs/${encodeURIComponent(runId)}/events`;
    // `withCredentials: false` because we'd rather 401 cleanly and fall
    // back to polling than send stale cookies. Cookie-auth deploys can
    // flip this if/when they ship.
    const es = new EventSource(url, { withCredentials: false });

    const queryKey = ["qa-dashboard", "runs", runId];

    es.onmessage = (ev: MessageEvent) => {
      let event: { type?: string } = {};
      try {
        event = JSON.parse(ev.data) as { type?: string };
      } catch {
        // Malformed event — ignore; the 15s polling fallback will reconcile.
        return;
      }
      // Any event invalidates the run-detail; the hook refetches once.
      queryClient.invalidateQueries({ queryKey });
      if (event.type === "done") {
        es.close();
      }
    };

    es.onerror = () => {
      // SSE failed (auth, network, server restart). Close cleanly so the
      // browser does NOT auto-reconnect — the 15s polling fallback in
      // useQaRun keeps the dashboard live.
      es.close();
    };

    return () => {
      es.close();
    };
  }, [runId, queryClient]);
}
