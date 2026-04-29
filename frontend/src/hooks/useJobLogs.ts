import { useEffect, useRef, useState, useCallback } from "react";
import type {
  LogEvent,
  PipelineGraph,
  NodeStateEvent,
  FeedbackTriggeredEvent,
  FeedbackResolvedEvent,
  AwaitingInputEvent,
} from "@/lib/types";
import { fetchJobLogEvents } from "@/api/logs";

const MAX_RECONNECT_RETRIES = 10;

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
  const [isConnected, setIsConnected] = useState(false);
  const [isComplete, setIsComplete] = useState(false);
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
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const isCompleteRef = useRef(false);
  const retryCountRef = useRef(0);
  const wsEverConnectedRef = useRef(false);

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
          setIsComplete(true);
          isCompleteRef.current = true;
          retryCountRef.current = 0;
          // Force flush pending events
          if (flushTimeoutRef.current) {
            clearTimeout(flushTimeoutRef.current);
            flushTimeoutRef.current = undefined;
          }
          setEvents([...eventsRef.current]);
          return;
        }

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

        if (event.type === "complete") {
          setIsComplete(true);
          isCompleteRef.current = true;
          retryCountRef.current = 0;
          // Force flush pending events
          if (flushTimeoutRef.current) {
            clearTimeout(flushTimeoutRef.current);
            flushTimeoutRef.current = undefined;
          }
          setEvents([...eventsRef.current]);
        }
      } catch {
        // Ignore malformed messages
      }
    };

    ws.onclose = () => {
      setIsConnected(false);
      if (!isCompleteRef.current && retryCountRef.current < MAX_RECONNECT_RETRIES) {
        retryCountRef.current += 1;
        // Buffer is intentionally preserved across reconnects. The backend's
        // since_seq replay cursor (Plan A) ensures events received after reconnect
        // are appended without duplicates. The buffer is only reset when jobId
        // changes (see useEffect cleanup above).
        reconnectTimeoutRef.current = setTimeout(connect, 3000);
      }
    };

    ws.onerror = () => ws.close();
  }, [jobId]);

  useEffect(() => {
    eventsRef.current = [];
    setEvents([]);
    setFallbackEvents([]);
    setIsComplete(false);
    isCompleteRef.current = false;
    retryCountRef.current = 0;
    wsEverConnectedRef.current = false;
    setCost(0);
    setTokensIn(0);
    setTokensOut(0);
    setTurns(0);
    setPipelineGraph(null);
    setNodeStates({});
    setFeedbackStates({});
    setPendingInputs({});
    setAutoAnsweredInputIds(new Set());
    connect();

    return () => {
      clearTimeout(reconnectTimeoutRef.current);
      clearTimeout(flushTimeoutRef.current);
      flushTimeoutRef.current = undefined;
      wsRef.current?.close();
    };
  }, [connect]);

  // Fallback: load persisted events for completed/failed jobs
  useEffect(() => {
    if (!jobId || isConnected || wsEverConnectedRef.current || events.length > 0) return;
    const timer = setTimeout(async () => {
      try {
        const persisted = await fetchJobLogEvents(jobId);
        if (persisted.length > 0) {
          setFallbackEvents(persisted);
        }
      } catch {
        // Ignore — no persisted events available
      }
    }, 1000);
    return () => clearTimeout(timer);
  }, [jobId, isConnected, events.length]);

  // Process fallback events for graph and node states
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
      if (event.type === "complete" || event.type === "stream_end") {
        setIsComplete(true);
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
