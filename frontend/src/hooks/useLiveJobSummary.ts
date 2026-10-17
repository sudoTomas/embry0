import { useRef, useState } from "react";
import type { LogEvent } from "@/lib/types";
import { useJobEventStream } from "./useJobEventStream";
import { eventNode } from "@/lib/wire";

/** Truncation cap for the one-line activity ticker (matches the defensive
 * summarization cap in ActivityPage.summarizeDetail). */
const MAX_ACTIVITY_LENGTH = 80;

export interface UseLiveJobSummaryResult {
  /** Latest meaningful event as a one-liner: the latest `progress` event
   * message, falling back to a summarized tool call. Null until either
   * arrives. */
  lastActivity: string | null;
  latestCost: number;
  latestTokensIn: number;
  latestTokensOut: number;
  /** Node id of the latest started pipeline stage/agent, null before the
   * first stage starts. */
  currentNode: string | null;
  /** 1-based start count of the current node — renders ultracode-style as
   * `node#attempt` (e.g. `review#2`). Same counting as useAgentStates. */
  attempt: number;
  isConnected: boolean;
  isComplete: boolean;
}

/** Summarize a tool invocation from the contract's `tool_call.summary`, the
 * backend's one-line detail (the file an Edit touched, the command Bash ran).
 * An empty summary degrades to the bare tool name. Format per the
 * live-console spec: `tool_call · Edit apps/recruit/notes.ts`. */
function summarizeToolCall(toolName: string, summary: string): string {
  let detail = summary;
  if (detail.length > MAX_ACTIVITY_LENGTH) {
    detail = `${detail.slice(0, MAX_ACTIVITY_LENGTH)}…`;
  }
  return detail ? `tool_call · ${toolName} ${detail}` : `tool_call · ${toolName}`;
}

/** Mutable per-connection-cycle accumulator; flushed to state in batches. */
interface LiveSummarySnapshot {
  lastProgress: string | null;
  lastToolCall: string | null;
  cost: number;
  tokensIn: number;
  tokensOut: number;
  currentNode: string | null;
  attempt: number;
}

function emptySnapshot(): LiveSummarySnapshot {
  return {
    lastProgress: null,
    lastToolCall: null,
    cost: 0,
    tokensIn: 0,
    tokensOut: 0,
    currentNode: null,
    attempt: 1,
  };
}

/**
 * Lightweight live summary of a job's event stream for console cards: keeps
 * only the latest meaningful activity line, cost/tokens, and the current
 * pipeline node — it does NOT buffer the full transcript (that's useJobLogs,
 * the drill-in view). Shares the WS connect/reconnect/REST-fallback core
 * with useJobLogs via useJobEventStream; one WS per running card is fine at
 * single-digit sandbox concurrency.
 */
export function useLiveJobSummary(jobId: string | undefined): UseLiveJobSummaryResult {
  const snapshotRef = useRef<LiveSummarySnapshot>(emptySnapshot());
  // Per-node start counts, mirroring useAgentStates' retry tracking: a
  // node's attempt number is how many times it has started.
  const nodeStartCountsRef = useRef(new Map<string, number>());
  const flushTimeoutRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const [summary, setSummary] = useState<LiveSummarySnapshot>(emptySnapshot());
  const [hasEvents, setHasEvents] = useState(false);

  const applyEvent = (event: LogEvent) => {
    const snapshot = snapshotRef.current;
    switch (event.type) {
      case "progress":
        snapshot.lastProgress = event.message;
        break;
      case "tool_call":
        snapshot.lastToolCall = summarizeToolCall(event.tool_name, event.summary);
        break;
      case "cost_update":
        snapshot.cost = event.cost_usd;
        snapshot.tokensIn = event.tokens_in;
        snapshot.tokensOut = event.tokens_out;
        break;
      case "node_started": {
        const node = eventNode(event);
        if (node) {
          const startCount = (nodeStartCountsRef.current.get(node) || 0) + 1;
          nodeStartCountsRef.current.set(node, startCount);
          snapshot.currentNode = node;
          snapshot.attempt = startCount;
        }
        break;
      }
      case "agent_started": {
        // Points at the active node but does not restart it, so the attempt
        // count is looked up rather than incremented.
        const node = eventNode(event);
        if (node) {
          snapshot.currentNode = node;
          snapshot.attempt = nodeStartCountsRef.current.get(node) || 1;
        }
        break;
      }
    }
  };

  const flushNow = () => {
    if (flushTimeoutRef.current) {
      clearTimeout(flushTimeoutRef.current);
      flushTimeoutRef.current = undefined;
    }
    setSummary({ ...snapshotRef.current });
  };

  const { isConnected, isComplete } = useJobEventStream(
    jobId,
    {
      onEvent: (event) => {
        applyEvent(event);
        setHasEvents(true);
        // Same 50 ms batch window as useJobLogs — one render per burst, not
        // one per event.
        if (!flushTimeoutRef.current) {
          flushTimeoutRef.current = setTimeout(() => {
            setSummary({ ...snapshotRef.current });
            flushTimeoutRef.current = undefined;
          }, 50);
        }
      },
      onStreamEnd: () => {
        // Force flush so the final cost/activity lands without waiting out
        // the batch window
        flushNow();
      },
      onFallbackEvents: (persisted) => {
        // WS never connected — reduce the persisted log through the same
        // per-event logic and publish in one shot.
        for (const event of persisted) {
          applyEvent(event);
        }
        flushNow();
      },
      onReset: () => {
        clearTimeout(flushTimeoutRef.current);
        flushTimeoutRef.current = undefined;
        snapshotRef.current = emptySnapshot();
        nodeStartCountsRef.current = new Map();
        setSummary(emptySnapshot());
        setHasEvents(false);
      },
    },
    hasEvents,
  );

  return {
    // Progress events are the narrator channel; tool calls are the fallback
    // when a pipeline emits none (spec: "prefer the latest 'progress' event
    // message, falling back to a summarized tool_call").
    lastActivity: summary.lastProgress ?? summary.lastToolCall,
    latestCost: summary.cost,
    latestTokensIn: summary.tokensIn,
    latestTokensOut: summary.tokensOut,
    currentNode: summary.currentNode,
    attempt: summary.attempt,
    isConnected,
    isComplete,
  };
}
