import { useEffect, useRef, useState } from "react";
import type { LogEvent, PipelineGraph } from "@/lib/types";
import { useJobEventStream } from "./useJobEventStream";

interface UseJobLogsResult {
  events: LogEvent[];
  isConnected: boolean;
  isComplete: boolean;
  latestCost: number;
  latestTokensIn: number;
  latestTokensOut: number;
  latestTurns: number;
  pipelineGraph: PipelineGraph | null;
}

export function useJobLogs(jobId: string | undefined): UseJobLogsResult {
  const eventsRef = useRef<LogEvent[]>([]);
  const [events, setEvents] = useState<LogEvent[]>([]);
  const flushTimeoutRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const [cost, setCost] = useState(0);
  const [tokensIn, setTokensIn] = useState(0);
  const [tokensOut, setTokensOut] = useState(0);
  const [turns, setTurns] = useState(0);
  const [pipelineGraph, setPipelineGraph] = useState<PipelineGraph | null>(null);
  const [fallbackEvents, setFallbackEvents] = useState<LogEvent[]>([]);

  const { isConnected, isComplete } = useJobEventStream(
    jobId,
    {
      onEvent: (event) => {
        eventsRef.current.push(event);
        if (!flushTimeoutRef.current) {
          flushTimeoutRef.current = setTimeout(() => {
            setEvents([...eventsRef.current]);
            flushTimeoutRef.current = undefined;
          }, 50);
        }

        if (event.type === "cost_update") {
          setCost(event.cost_usd);
          setTokensIn(event.tokens_in);
          setTokensOut(event.tokens_out);
          setTurns(event.num_turns);
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
        setCost(0);
        setTokensIn(0);
        setTokensOut(0);
        setTurns(0);
        setPipelineGraph(null);
      },
    },
    events.length > 0,
  );

  // Process fallback events for the graph and cost (isComplete for
  // persisted terminal events is handled inside useJobEventStream)
  useEffect(() => {
    if (fallbackEvents.length === 0) return;
    for (const event of fallbackEvents) {
      if (event.type === "pipeline_graph") {
        setPipelineGraph(event.graph);
      }
      if (event.type === "cost_update") {
        setCost(event.cost_usd);
        setTokensIn(event.tokens_in);
        setTokensOut(event.tokens_out);
        setTurns(event.num_turns);
      }
    }
  }, [fallbackEvents]);

  return {
    events: events.length > 0 ? events : fallbackEvents,
    isConnected,
    isComplete,
    latestCost: cost,
    latestTokensIn: tokensIn,
    latestTokensOut: tokensOut,
    latestTurns: turns,
    pipelineGraph,
  };
}
