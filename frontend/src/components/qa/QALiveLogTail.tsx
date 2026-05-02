import { useEffect, useRef, useState } from "react";
import type { JSX } from "react";
import { logStreamUrl } from "@/api/qa-artifacts";

interface Props {
  jobId: string;
  service: string;
  active: boolean; // false when job has terminated; closes the EventSource
}

type StreamState = "connecting" | "open" | "closed";

export function QALiveLogTail({ jobId, service, active }: Props): JSX.Element {
  const [lines, setLines] = useState<string[]>([]);
  const [streamState, setStreamState] = useState<StreamState>("connecting");
  const containerRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (!active) {
      setStreamState("closed");
      return;
    }
    setStreamState("connecting");
    const es = new EventSource(logStreamUrl(jobId, service));
    es.onopen = () => setStreamState("open");
    es.onmessage = (e) => {
      setLines((prev) => {
        const next = [...prev, e.data];
        // Cap to 1000 lines to avoid unbounded memory.
        // TODO(perf): .slice(-1000) allocates a new array per line; switch to
        // a ring buffer if log throughput becomes a problem.
        return next.length > 1000 ? next.slice(-1000) : next;
      });
    };
    es.onerror = () => {
      es.close();
      setStreamState("closed");
    };
    return () => {
      es.close();
      setStreamState("closed");
    };
  }, [jobId, service, active]);

  // Auto-scroll on new lines.
  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [lines]);

  return (
    <pre
      ref={containerRef}
      className="relative font-mono text-[11px] bg-black/40 border border-white/10 rounded p-2 h-72 overflow-y-auto whitespace-pre-wrap"
    >
      {lines.length === 0 ? (
        <span className="text-white/40">Waiting for logs from {service}...</span>
      ) : (
        lines.join("\n")
      )}
      {streamState === "closed" && (
        <span className="absolute bottom-1 right-2 text-[10px] uppercase tracking-wide text-white/40">
          stream closed
        </span>
      )}
    </pre>
  );
}
