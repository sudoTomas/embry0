import { useEffect, useMemo, useRef, useState } from "react";
import type { LogEvent, PipelineGraph } from "@/lib/types";
import { applyTotals, emptyTotals, reduceTotals, totalsOf, type Totals } from "@/lib/eventTotals";
import { useJobEventStream } from "./useJobEventStream";

interface UseJobLogsResult {
  events: LogEvent[];
  isConnected: boolean;
  isComplete: boolean;
  /** Job totals summed across runs (lib/eventTotals), not the last event's values. */
  latestCost: number;
  latestTokensIn: number;
  latestTokensOut: number;
  /** Top-level turns summed over runs, from `turn_start`. */
  latestTurns: number;
  pipelineGraph: PipelineGraph | null;
}

export function useJobLogs(jobId: string | undefined): UseJobLogsResult {
  const eventsRef = useRef<LogEvent[]>([]);
  const [events, setEvents] = useState<LogEvent[]>([]);
  const flushTimeoutRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const totalsRef = useRef(emptyTotals());
  const [totals, setTotals] = useState<Totals>(() => totalsOf(emptyTotals()));
  const [pipelineGraph, setPipelineGraph] = useState<PipelineGraph | null>(null);
  const [fallbackEvents, setFallbackEvents] = useState<LogEvent[]>([]);

  const { isConnected, isComplete } = useJobEventStream(
    jobId,
    {
      onEvent: (event) => {
        eventsRef.current.push(event);
        applyTotals(totalsRef.current, event);
        if (!flushTimeoutRef.current) {
          flushTimeoutRef.current = setTimeout(() => {
            setEvents([...eventsRef.current]);
            setTotals(totalsOf(totalsRef.current));
            flushTimeoutRef.current = undefined;
          }, 50);
        }

        // Graph-specific events
        if (event.type === "pipeline_graph") {
          setPipelineGraph(event.graph);
        }
      },
      onStreamEnd: () => {
        // Force flush pending events
        if (flushTimeoutRef.current) {
          clearTimeout(flushTimeoutRef.current);
          flushTimeoutRef.current = undefined;
        }
        setEvents([...eventsRef.current]);
        setTotals(totalsOf(totalsRef.current));
      },
      onFallbackEvents: (persisted) => {
        setFallbackEvents(persisted);
      },
      onReset: () => {
        clearTimeout(flushTimeoutRef.current);
        flushTimeoutRef.current = undefined;
        eventsRef.current = [];
        setEvents([]);
        setFallbackEvents([]);
        totalsRef.current = emptyTotals();
        setTotals(totalsOf(totalsRef.current));
        setPipelineGraph(null);
      },
    },
    events.length > 0,
  );

  // Process fallback events for the graph (isComplete for persisted terminal
  // events is handled inside useJobEventStream)
  useEffect(() => {
    if (fallbackEvents.length === 0) return;
    for (const event of fallbackEvents) {
      if (event.type === "pipeline_graph") {
        setPipelineGraph(event.graph);
      }
    }
  }, [fallbackEvents]);

  // Totals follow whichever event list is shown: the live stream, or the
  // persisted log when the WS never connected.
  const fallbackTotals = useMemo(() => reduceTotals(fallbackEvents), [fallbackEvents]);
  const shownTotals = events.length > 0 ? totals : fallbackTotals;

  return {
    events: events.length > 0 ? events : fallbackEvents,
    isConnected,
    isComplete,
    latestCost: shownTotals.costUsd,
    latestTokensIn: shownTotals.tokensIn,
    latestTokensOut: shownTotals.tokensOut,
    latestTurns: shownTotals.turns,
    pipelineGraph,
  };
}
