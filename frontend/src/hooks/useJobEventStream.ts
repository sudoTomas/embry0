import { useEffect, useRef, useState, useCallback } from "react";
import type { LogEvent } from "@/lib/types";
import { fetchJobLogEvents } from "@/api/logs";

const MAX_RECONNECT_RETRIES = 10;

/**
 * Consumer callbacks for {@link useJobEventStream}. Handlers are read through
 * a latest-ref, so passing a fresh object literal on every render is fine —
 * it never tears down or reconnects the WebSocket.
 */
export interface JobEventStreamHandlers {
  /** Called for every parsed live WS event except `stream_end` (a pure
   * stream marker that never enters consumer state). */
  onEvent: (event: LogEvent) => void;
  /** Called when the stream terminates (`stream_end` or `complete` event) so
   * consumers can force-flush any batched state immediately. For a
   * `complete` event this fires after `onEvent` has delivered the event. */
  onStreamEnd?: () => void;
  /** Called with persisted events when the REST fallback loads them (the WS
   * never connected — typically a job that finished before mount). */
  onFallbackEvents?: (events: LogEvent[]) => void;
  /** Called on teardown of each connection cycle (jobId change or unmount).
   * Consumers reset event-derived state and clear batch timers here.
   * Reconnects within one cycle intentionally do NOT reset: the backend's
   * since_seq replay cursor (Plan A) ensures events received after reconnect
   * are appended without duplicates. */
  onReset?: () => void;
}

export interface JobEventStreamResult {
  isConnected: boolean;
  isComplete: boolean;
}

/**
 * Shared WS connection core for per-job event streams: connects to
 * `/ws/jobs/{jobId}/events`, reconnects with a retry cap while the stream is
 * live, and falls back to the persisted REST log when the WS never connects.
 *
 * Consumers (`useJobLogs`, `useLiveJobSummary`) own all event-derived state
 * via the handler callbacks; this hook owns only the connection lifecycle.
 *
 * @param hasEvents whether the consumer has already received live events —
 *   suppresses the REST fallback so live data is never clobbered by a
 *   stale persisted snapshot.
 */
export function useJobEventStream(
  jobId: string | undefined,
  handlers: JobEventStreamHandlers,
  hasEvents: boolean,
): JobEventStreamResult {
  const [isConnected, setIsConnected] = useState(false);
  const [isComplete, setIsComplete] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const isCompleteRef = useRef(false);
  const retryCountRef = useRef(0);
  const wsEverConnectedRef = useRef(false);
  // Latest-ref pattern: handlers change identity every render; routing them
  // through a ref keeps `connect` stable on jobId alone.
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  const markStreamComplete = useCallback(() => {
    setIsComplete(true);
    isCompleteRef.current = true;
    retryCountRef.current = 0;
    handlersRef.current.onStreamEnd?.();
  }, []);

  const connect = useCallback(() => {
    if (!jobId) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const apiKey = import.meta.env.VITE_API_KEY as string | undefined;
    const subprotocols = apiKey ? [`athanor.bearer.${apiKey}`] : [];
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/jobs/${jobId}/events`, subprotocols);
    wsRef.current = ws;

    ws.onopen = () => {
      setIsConnected(true);
      wsEverConnectedRef.current = true;
      retryCountRef.current = 0;
    };

    ws.onmessage = (e) => {
      try {
        const event: LogEvent = JSON.parse(e.data);

        if (event.type === "stream_end") {
          markStreamComplete();
          return;
        }

        handlersRef.current.onEvent(event);

        if (event.type === "complete") {
          markStreamComplete();
        }
      } catch {
        // Ignore malformed messages
      }
    };

    ws.onclose = () => {
      setIsConnected(false);
      if (!isCompleteRef.current && retryCountRef.current < MAX_RECONNECT_RETRIES) {
        retryCountRef.current += 1;
        // Consumer buffers are intentionally preserved across reconnects (see
        // onReset docs above). Buffers reset only when jobId changes, via the
        // effect cleanup below.
        reconnectTimeoutRef.current = setTimeout(connect, 3000);
      }
    };

    ws.onerror = () => ws.close();
  }, [jobId, markStreamComplete]);

  useEffect(() => {
    setIsConnected(false);
    setIsComplete(false);
    isCompleteRef.current = false;
    retryCountRef.current = 0;
    wsEverConnectedRef.current = false;
    connect();

    return () => {
      clearTimeout(reconnectTimeoutRef.current);
      const ws = wsRef.current;
      if (ws) {
        // Detach handlers before closing: the browser fires onclose
        // asynchronously after close(), which would otherwise schedule a
        // zombie reconnect against the previous jobId.
        ws.onopen = null;
        ws.onmessage = null;
        ws.onclose = null;
        ws.onerror = null;
        ws.close();
      }
      handlersRef.current.onReset?.();
    };
  }, [connect]);

  // Fallback: load persisted events for completed/failed jobs
  useEffect(() => {
    if (!jobId || isConnected || wsEverConnectedRef.current || hasEvents) return;
    const timer = setTimeout(async () => {
      try {
        const persisted = await fetchJobLogEvents(jobId);
        if (persisted.length > 0) {
          // A persisted terminal event means the stream is over. Only the
          // isComplete state flips — isCompleteRef keeps its live-path
          // reconnect-gating semantics.
          if (persisted.some((ev) => ev.type === "complete" || ev.type === "stream_end")) {
            setIsComplete(true);
          }
          handlersRef.current.onFallbackEvents?.(persisted);
        }
      } catch {
        // Ignore — no persisted events available
      }
    }, 1000);
    return () => clearTimeout(timer);
  }, [jobId, isConnected, hasEvents]);

  return { isConnected, isComplete };
}
