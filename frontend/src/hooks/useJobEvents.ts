import { useState, useEffect, useRef, useCallback } from "react";
import type { JobEvent } from "@/lib/types/jobs";

const MAX_RETRIES = 10;

export function useJobEvents(jobId: string | undefined, enabled: boolean = true) {
  const [events, setEvents] = useState<JobEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const retryCountRef = useRef(0);

  const connect = useCallback(() => {
    if (!jobId || !enabled) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const url = `${protocol}//${window.location.host}/ws/jobs/${jobId}/events`;

    const ws = new WebSocket(url);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      retryCountRef.current = 0;
    };

    ws.onmessage = (msg) => {
      try {
        const event: JobEvent = JSON.parse(msg.data);
        setEvents((prev) => [...prev, event]);
      } catch {
        // ignore malformed messages
      }
    };

    ws.onclose = () => {
      setConnected(false);
      wsRef.current = null;
      // Reconnect after delay if still enabled and under retry limit
      if (enabled && jobId && retryCountRef.current < MAX_RETRIES) {
        retryCountRef.current += 1;
        setTimeout(() => {
          connect();
        }, 3000);
      }
    };

    ws.onerror = () => {
      setConnected(false);
    };
  }, [jobId, enabled]);

  useEffect(() => {
    retryCountRef.current = 0;
    connect();
    return () => {
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [connect]);

  return { events, connected };
}
