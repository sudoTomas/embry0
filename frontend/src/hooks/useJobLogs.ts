import { useEffect, useRef, useState } from "react";
import type {
  LogEvent,
  PipelineGraph,
  NodeStateEvent,
  FeedbackTriggeredEvent,
  FeedbackResolvedEvent,
  AwaitingInputEvent,
} from "@/lib/types";
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
  nodeStates: Record<string, NodeStateEvent>;
  feedbackStates: Record<string, FeedbackTriggeredEvent>;
  pendingInputs: Record<string, AwaitingInputEvent>;
  autoAnsweredInputIds: Set<string>;
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
  const [nodeStates, setNodeStates] = useState<Record<string, NodeStateEvent>>({});
  const [feedbackStates, setFeedbackStates] = useState<Record<string, FeedbackTriggeredEvent>>({});
  const [pendingInputs, setPendingInputs] = useState<Record<string, AwaitingInputEvent>>({});
  const [autoAnsweredInputIds, setAutoAnsweredInputIds] = useState<Set<string>>(new Set());
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
        if (event.type === "complete") {
          if (event.cost_usd != null) setCost(event.cost_usd);
          if (event.tokens_in != null) setTokensIn(event.tokens_in);
          if (event.tokens_out != null) setTokensOut(event.tokens_out);
          if (event.turns != null) setTurns(event.turns);
        }

        // Graph-specific events
        if (event.type === "pipeline_graph") {
          setPipelineGraph(event.graph);
        }
        if (event.type === "node_state") {
          setNodeStates((prev) => ({ ...prev, [event.node_id]: event }));
        }
        if (event.type === "feedback_triggered") {
          setFeedbackStates((prev) => ({ ...prev, [event.edge_id]: event }));
        }
        if (event.type === "feedback_resolved") {
          const { edge_id } = event as FeedbackResolvedEvent;
          setFeedbackStates((prev) => {
            const next = { ...prev };
            delete next[edge_id];
            return next;
          });
        }

        // Skill responder input events
        if (event.type === "awaiting_input") {
          setPendingInputs((prev) => ({ ...prev, [event.input_id]: event }));
        }
        if (event.type === "auto_answered") {
          setAutoAnsweredInputIds((prev) => new Set(prev).add(event.input_id));
        }
        if (event.type === "input_resumed") {
          setPendingInputs((prev) => {
            const next = { ...prev };
            delete next[event.input_id];
            return next;
          });
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
        setNodeStates({});
        setFeedbackStates({});
        setPendingInputs({});
        setAutoAnsweredInputIds(new Set());
      },
    },
    events.length > 0,
  );

  // Process fallback events for graph and node states (isComplete for
  // persisted terminal events is handled inside useJobEventStream)
  useEffect(() => {
    if (fallbackEvents.length === 0) return;
    for (const event of fallbackEvents) {
      if (event.type === "pipeline_graph") {
        setPipelineGraph(event.graph);
      }
      if (event.type === "node_state") {
        setNodeStates((prev) => ({
          ...prev,
          [event.node_id]: event,
        }));
      }
      if (event.type === "cost_update") {
        setCost(event.cost_usd);
        setTokensIn(event.tokens_in);
        setTokensOut(event.tokens_out);
        setTurns(event.num_turns);
      }
      if (event.type === "complete") {
        if (event.cost_usd != null) setCost(event.cost_usd);
        if (event.tokens_in != null) setTokensIn(event.tokens_in);
        if (event.tokens_out != null) setTokensOut(event.tokens_out);
        if (event.turns != null) setTurns(event.turns);
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
    nodeStates,
    feedbackStates,
    pendingInputs,
    autoAnsweredInputIds,
  };
}
