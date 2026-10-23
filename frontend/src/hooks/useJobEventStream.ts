import { useEffect, useRef, useState, useCallback } from "react";
import type { LogEvent } from "@/lib/types";
import { fetchJobLogEvents } from "@/api/logs";
import { fetchJobEventsToken } from "@/api/jobs";
import { normalizeWireEvent } from "@/lib/wire";

const MAX_RECONNECT_RETRIES = 10;
/** RFC 6455 normal closure: the server ended the stream on purpose (spec 3.5). */
const FINISHED_CLOSE_CODE = 1000;

/**
 * Consumer callbacks for {@link useJobEventStream}. Handlers are read through
 * a latest-ref, so passing a fresh object literal on every render is fine —
 * it never tears down or reconnects the WebSocket.
 */
export interface JobEventStreamHandlers {
  /** Called once for every event, in `event_seq` order, live or gap-filled. */
  onEvent: (event: LogEvent) => void;
  /** Called when the stream terminates (`job_finished`, or the server closed
   * with 1000) so consumers can flush batched state immediately. For a
   * `job_finished` event this fires after `onEvent` has delivered it. */
  onStreamEnd?: () => void;
  /** Called with persisted events when the REST fallback loads them (the WS
   * never connected — typically a job that finished before mount). */
  onFallbackEvents?: (events: LogEvent[]) => void;
  /** Called on teardown of each connection cycle (jobId change or unmount).
   * Reconnects within one cycle do NOT reset: the hook reconnects with
   * `?since_seq=<last event_seq>` and drops anything it already delivered. */
  onReset?: () => void;
}

export interface JobEventStreamResult {
  isConnected: boolean;
  isComplete: boolean;
}

/**
 * Shared WS connection core for per-job event streams.
 *
 * Replay and gaps (spec 3.5): the hook remembers the highest `event_seq` it
 * delivered. A reconnect asks the server for `?since_seq=<that>`; a live or
 * replayed event at or below it is a duplicate and is dropped. `event_seq`
 * is `job_logs.id`, shared by every job, so one job's seqs are not
 * contiguous and a larger seq alone means nothing; the server stamps
 * `prev_seq`, the job's previous persisted seq, and a live event whose
 * `prev_seq` is above the cursor means this job had an event the hook never
 * got (bus overflow, a persist failure), so the hook fetches the persisted
 * log from the cursor and delivers it first, holding later live events until
 * the fill lands. A new connection drops a fill still in flight: its replay
 * covers the same rows. `job_finished`, or a server close with code 1000,
 * ends the stream: the hook marks it complete and never reconnects.
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
  const connectAttemptRef = useRef(0);
  /** Highest event_seq delivered to the consumer; the reconnect cursor. */
  const lastSeqRef = useRef(0);
  /** A gap fill in flight; live events queue behind it. */
  const fillRef = useRef<Promise<void> | null>(null);
  const queuedRef = useRef<LogEvent[]>([]);
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  const markStreamComplete = useCallback(() => {
    if (isCompleteRef.current) return;
    setIsComplete(true);
    isCompleteRef.current = true;
    retryCountRef.current = 0;
    handlersRef.current.onStreamEnd?.();
  }, []);

  /** Deliver one event unless its seq was already delivered; advance the cursor. */
  const deliver = useCallback(
    (event: LogEvent) => {
      const seq = event.event_seq;
      if (seq !== undefined) {
        if (seq <= lastSeqRef.current) return;
        lastSeqRef.current = seq;
      }
      handlersRef.current.onEvent(event);
      if (event.type === "job_finished") markStreamComplete();
    },
    [markStreamComplete],
  );

  const applyLive = useCallback(
    (event: LogEvent, currentJobId: string) => {
      const seq = event.event_seq;
      if (seq !== undefined && seq <= lastSeqRef.current) return; // replayed duplicate
      if (fillRef.current) {
        queuedRef.current.push(event); // a fill owns the order until it lands
        return;
      }
      const prevSeq = event.prev_seq;
      const gap = prevSeq !== undefined && lastSeqRef.current > 0 && prevSeq > lastSeqRef.current;
      if (!gap) {
        deliver(event);
        return;
      }
      queuedRef.current.push(event);
      const from = lastSeqRef.current;
      // A teardown (jobId change, unmount) clears fillRef; a fill that lands
      // after it belongs to the previous cycle and must deliver nothing.
      const fill: Promise<void> = fetchJobLogEvents(currentJobId, from)
        .then((persisted) => {
          if (fillRef.current !== fill) return;
          for (const p of persisted) deliver(p);
        })
        .catch(() => {
          // The live event still lands below; the gap stays a gap.
        })
        .finally(() => {
          if (fillRef.current !== fill) return;
          fillRef.current = null;
          const queued = queuedRef.current;
          queuedRef.current = [];
          for (const q of queued) deliver(q);
        });
      fillRef.current = fill;
    },
    [deliver],
  );

  const connect = useCallback(() => {
    if (!jobId) return;
    const attempt = ++connectAttemptRef.current;

    void (async () => {
      // RAV-1070: trade the Bearer key for a short-lived job-scoped token —
      // the raw API key never rides the WS subprotocol. A failed mint
      // connects bare: dev-mode servers accept it, production closes the
      // socket and the reconnect/REST-fallback paths take over.
      let subprotocols: string[] = [];
      try {
        const { token } = await fetchJobEventsToken(jobId);
        subprotocols = [`embry0.token.${token}`];
      } catch {
        // fall through — see above
      }
      if (attempt !== connectAttemptRef.current) return;

      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const cursor = lastSeqRef.current > 0 ? `?since_seq=${lastSeqRef.current}` : "";
      const ws = new WebSocket(`${protocol}//${window.location.host}/ws/jobs/${jobId}/events${cursor}`, subprotocols);
      wsRef.current = ws;

      ws.onopen = () => {
        setIsConnected(true);
        wsEverConnectedRef.current = true;
        retryCountRef.current = 0;
        // A fill from the previous connection must not hold this one's replay:
        // the replay starts at the same cursor, so it brings the same rows.
        fillRef.current = null;
        queuedRef.current = [];
      };

      ws.onmessage = (e) => {
        try {
          const event = normalizeWireEvent(JSON.parse(e.data));
          applyLive(event, jobId);
        } catch {
          // Ignore malformed messages
        }
      };

      ws.onclose = (e) => {
        setIsConnected(false);
        if (e.code === FINISHED_CLOSE_CODE) {
          // The server closed on purpose: the job is over (spec 3.5).
          markStreamComplete();
          return;
        }
        if (!isCompleteRef.current && retryCountRef.current < MAX_RECONNECT_RETRIES) {
          retryCountRef.current += 1;
          reconnectTimeoutRef.current = setTimeout(connect, 3000);
        }
      };

      ws.onerror = () => ws.close();
    })();
  }, [jobId, applyLive, markStreamComplete]);

  useEffect(() => {
    setIsConnected(false);
    setIsComplete(false);
    isCompleteRef.current = false;
    retryCountRef.current = 0;
    wsEverConnectedRef.current = false;
    lastSeqRef.current = 0;
    fillRef.current = null;
    queuedRef.current = [];
    connect();

    return () => {
      clearTimeout(reconnectTimeoutRef.current);
      // eslint-disable-next-line react-hooks/exhaustive-deps
      connectAttemptRef.current++;
      const ws = wsRef.current;
      if (ws) {
        ws.onopen = null;
        ws.onmessage = null;
        ws.onclose = null;
        ws.onerror = null;
        ws.close();
      }
      fillRef.current = null;
      queuedRef.current = [];
      handlersRef.current.onReset?.();
    };
  }, [connect]);

  // Fallback: load persisted events for a job whose socket never connected.
  useEffect(() => {
    if (!jobId || isConnected || wsEverConnectedRef.current || hasEvents) return;
    const timer = setTimeout(async () => {
      try {
        const persisted = await fetchJobLogEvents(jobId, 0);
        if (persisted.length > 0) {
          for (const ev of persisted) {
            const seq = ev.event_seq;
            if (seq !== undefined && seq > lastSeqRef.current) lastSeqRef.current = seq;
          }
          if (persisted.some((ev) => ev.type === "job_finished")) {
            setIsComplete(true);
            isCompleteRef.current = true;
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
