import { useMemo, useRef, useEffect, useState, useCallback } from "react";
import {
  ChevronRight,
  ChevronDown,
  Bot,
  Wrench,
  Brain,
  AlertCircle,
} from "lucide-react";
import { cn, highlightJson } from "@/lib/utils";
import type { LogEvent } from "@/lib/types";
import { Sprite } from "@/components/roster";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import type { AgentIdentity } from "@/lib/agentIdentity";
import { sessionAgentType } from "@/lib/agentSession";

interface ConversationViewProps {
  events: LogEvent[];
  /**
   * Offered on the divider of an agent's latest finished run when the stream
   * truncated events of any of that agent's finished runs: the stored session
   * is one per job and agent type, merged across that agent's runs.
   */
  onFullTranscript?: (run: { runId: string; agent: string }) => void;
}

interface ConversationTextBlock {
  type: "text";
  text: string;
}

interface ConversationThinkingBlock {
  type: "thinking";
  thinking: string;
}

interface ConversationToolUseBlock {
  type: "tool_use";
  id: string;
  name: string;
  summary: string;
  input: Record<string, unknown>;
}

interface ConversationToolResultBlock {
  type: "tool_result";
  tool_use_id: string;
  content: string;
  is_error: boolean;
}

type ConversationContentBlock =
  | ConversationTextBlock
  | ConversationThinkingBlock
  | ConversationToolUseBlock
  | ConversationToolResultBlock;

/** A single conversation turn grouped from events. */
interface ConversationTurn {
  /** The run's own turn_number; null for events before the run's first turn_start (pre-contract rows). */
  turnNumber: number | null;
  /** `Run N · node · agent`, set on the first turn of each run. */
  divider?: string;
  runId?: string;
  node?: string;
  agent?: string;
  assistantBlocks: ConversationContentBlock[];
  userBlocks: ConversationContentBlock[];
  model?: string;
  timestamp: string;
}

const INITIAL_TURNS_SHOWN = 20;
const LOAD_MORE_COUNT = 20;

export function ConversationView({ events, onFullTranscript }: ConversationViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [userScrolledUp, setUserScrolledUp] = useState(false);
  const [turnsShown, setTurnsShown] = useState(INITIAL_TURNS_SHOWN);
  const identityFor = useIdentityLookup();

  // Group events into turns: turn_start opens one for its run, and the
  // thinking, text, tool_call and tool_result events of that run go into it.
  // A tool_result joins the turn that holds its tool_call. Alongside, the
  // runs offered a stored session: the latest finished run of each agent one
  // of whose finished runs the stream truncated (the session row is per
  // agent, not per run).
  const { turns, offeredRuns } = useMemo(() => {
    const result: ConversationTurn[] = [];
    const finishedRuns = new Set<string>();
    const truncatedRuns = new Set<string>();
    const agentByRun = new Map<string, string>();
    const currentByRun = new Map<string, ConversationTurn>();
    const turnByToolId = new Map<string, ConversationTurn>();

    const open = (event: LogEvent, turnNumber: number | null, model?: string) => {
      const turn: ConversationTurn = {
        turnNumber,
        runId: event.run_id,
        node: event.node,
        agent: event.agent,
        assistantBlocks: [],
        userBlocks: [],
        timestamp: event.timestamp,
        model,
      };
      result.push(turn);
      currentByRun.set(event.run_id ?? "", turn);
      if (event.run_id && event.agent) agentByRun.set(event.run_id, event.agent);
      return turn;
    };
    const turnFor = (event: LogEvent) => currentByRun.get(event.run_id ?? "") ?? open(event, null);

    for (const event of events) {
      if (event.run_id && "truncated" in event && event.truncated) truncatedRuns.add(event.run_id);
      switch (event.type) {
        case "agent_completed":
          if (event.run_id) finishedRuns.add(event.run_id);
          break;
        case "turn_start":
          open(event, event.turn_number, event.model);
          break;
        case "thinking":
          if (event.text) turnFor(event).assistantBlocks.push({ type: "thinking", thinking: event.text });
          break;
        case "text":
          if (event.text) turnFor(event).assistantBlocks.push({ type: "text", text: event.text });
          break;
        case "tool_call": {
          const turn = turnFor(event);
          turn.assistantBlocks.push({
            type: "tool_use",
            id: event.tool_id,
            name: event.tool_name,
            summary: event.summary,
            input: event.tool_input,
          });
          turnByToolId.set(event.tool_id, turn);
          break;
        }
        case "tool_result":
          (turnByToolId.get(event.tool_id) ?? turnFor(event)).userBlocks.push({
            type: "tool_result",
            tool_use_id: event.tool_id,
            content: event.content,
            is_error: event.is_error,
          });
          break;
      }
    }

    const runNumbers = new Map<string, number>();
    let previousRunId: string | undefined;
    for (const turn of result) {
      if (turn.runId && turn.runId !== previousRunId) {
        if (!runNumbers.has(turn.runId)) runNumbers.set(turn.runId, runNumbers.size + 1);
        turn.divider = [`Run ${runNumbers.get(turn.runId)}`, turn.node, turn.agent].filter(Boolean).join(" · ");
      }
      previousRunId = turn.runId;
    }

    // finishedRuns iterates in completion order, so the last write per agent wins.
    const latestFinishedByAgent = new Map<string, string>();
    const truncatedAgents = new Set<string>();
    for (const runId of finishedRuns) {
      const agent = agentByRun.get(runId);
      if (!agent) continue;
      latestFinishedByAgent.set(sessionAgentType(agent), runId);
      if (truncatedRuns.has(runId)) truncatedAgents.add(sessionAgentType(agent));
    }
    const offeredRuns = new Set([...truncatedAgents].map((agent) => latestFinishedByAgent.get(agent)!));
    return { turns: result, offeredRuns };
  }, [events]);

  const hasMore = turnsShown < turns.length;
  const visibleTurns = turns.slice(Math.max(0, turns.length - turnsShown));

  const handleScroll = useCallback(() => {
    const el = containerRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    setUserScrolledUp(!atBottom);
  }, []);

  useEffect(() => {
    if (!userScrolledUp && containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight;
    }
  }, [turns, userScrolledUp]);

  if (turns.length === 0) {
    return (
      <div className="flex items-center justify-center h-40 text-muted-foreground text-sm">
        No conversation messages captured yet
      </div>
    );
  }

  return (
    <div
      ref={containerRef}
      className="space-y-1 overflow-auto overscroll-contain flex-1 min-h-0 max-h-[70vh] lg:max-h-none"
      onScroll={handleScroll}
    >
      {hasMore && (
        <button
          className="w-full py-2 text-xs text-muted-foreground hover:text-foreground transition-colors"
          onClick={() => setTurnsShown((n) => n + LOAD_MORE_COUNT)}
        >
          Load {Math.min(LOAD_MORE_COUNT, turns.length - turnsShown)} earlier turns...
        </button>
      )}

      {visibleTurns.map((turn, i) => (
        <TurnCard
          key={turns.length - visibleTurns.length + i}
          turn={turn}
          identityFor={identityFor}
          offeredRuns={offeredRuns}
          onFullTranscript={onFullTranscript}
        />
      ))}
    </div>
  );
}

function TurnCard({
  turn,
  identityFor,
  offeredRuns,
  onFullTranscript,
}: {
  turn: ConversationTurn;
  identityFor: (type: string) => AgentIdentity;
  offeredRuns: Set<string>;
  onFullTranscript?: (run: { runId: string; agent: string }) => void;
}) {
  return (
    <div className="space-y-1">
      {turn.divider && (
        <div data-testid="run-divider" className="flex items-center gap-2 pt-3">
          <div className="h-px flex-1 bg-border" />
          {turn.agent && (
            <Sprite avatar={identityFor(turn.agent).avatar} color={identityFor(turn.agent).color} state="idle" size={16} still decorative />
          )}
          <span className="text-xs text-muted-foreground font-mono">
            {turn.divider}
            {turn.agent && <span className="ml-1.5" style={{ color: identityFor(turn.agent).hex }}>{identityFor(turn.agent).displayName}</span>}
          </span>
          {turn.runId && turn.agent && onFullTranscript && offeredRuns.has(turn.runId) && (
            <button type="button" onClick={() => onFullTranscript({ runId: turn.runId!, agent: turn.agent! })} className="text-xs underline text-muted-foreground hover:text-foreground">
              Full transcript
            </button>
          )}
          <div className="h-px flex-1 bg-border" />
        </div>
      )}
      {/* Turn separator */}
      <div className="flex items-center gap-2 pt-2">
        <div className="h-px flex-1 bg-border/40" />
        <span className="text-[10px] text-muted-foreground font-mono">
          {turn.turnNumber == null ? "No turn marker" : <>Turn {turn.turnNumber}</>}
          {turn.model && <span className="ml-1.5 opacity-60">{turn.model}</span>}
        </span>
        <div className="h-px flex-1 bg-border/40" />
      </div>

      {/* Assistant message */}
      {turn.assistantBlocks.length > 0 && (
        <div className="pl-1">
          <div className="flex items-center gap-1.5 mb-1">
            <Bot className="h-3.5 w-3.5 text-fg-secondary" />
            <span className="text-xs font-medium text-foreground">Assistant</span>
          </div>
          <div className="space-y-1 pl-5">
            {turn.assistantBlocks.map((block, i) => (
              <ContentBlockRenderer key={i} block={block} />
            ))}
          </div>
        </div>
      )}

      {/* User message (tool results) */}
      {turn.userBlocks.length > 0 && (
        <div className="pl-1">
          <div className="flex items-center gap-1.5 mb-1">
            <Wrench className="h-3.5 w-3.5 text-muted-foreground" />
            <span className="text-xs font-medium text-muted-foreground">Tool Results</span>
          </div>
          <div className="space-y-1 pl-5">
            {turn.userBlocks.map((block, i) => (
              <ContentBlockRenderer key={i} block={block} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

function ContentBlockRenderer({ block }: { block: ConversationContentBlock }) {
  switch (block.type) {
    case "text":
      return <TextBlockView text={block.text} />;
    case "thinking":
      return <ThinkingBlockView thinking={block.thinking} />;
    case "tool_use":
      return <ToolUseBlockView block={block} />;
    case "tool_result":
      return <ToolResultBlockView block={block} />;
    default:
      return null;
  }
}

function TextBlockView({ text }: { text: string }) {
  if (!text.trim()) return null;
  return (
    <div className="text-sm whitespace-pre-wrap leading-relaxed">
      {text}
    </div>
  );
}

function ThinkingBlockView({ thinking }: { thinking: string }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border border-border/50 rounded-md bg-muted/20">
      <button
        className="flex items-center gap-2 w-full px-2.5 py-1.5 text-xs hover:bg-muted/30 cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        <Brain className="h-3 w-3 text-muted-foreground shrink-0" />
        <span className="text-muted-foreground">Thinking</span>
        <span className="text-[10px] text-muted-foreground/60 ml-1">
          ({thinking.length.toLocaleString()} chars)
        </span>
        {expanded
          ? <ChevronDown className="h-3 w-3 ml-auto shrink-0 text-muted-foreground" />
          : <ChevronRight className="h-3 w-3 ml-auto shrink-0 text-muted-foreground" />
        }
      </button>
      {expanded && (
        <div className="px-2.5 pb-2 border-t border-border/30">
          <pre className="text-xs font-mono whitespace-pre-wrap text-muted-foreground mt-1.5 max-h-80 overflow-y-auto">
            {thinking}
          </pre>
        </div>
      )}
    </div>
  );
}

function ToolUseBlockView({ block }: { block: ConversationToolUseBlock }) {
  const [expanded, setExpanded] = useState(false);
  // Computed once per input: the page re-renders on every stream batch, and a
  // collapsed block must not re-stringify and re-highlight each time.
  const segments = useMemo(() => highlightJson(JSON.stringify(block.input, null, 2)), [block.input]);

  return (
    <div className="border border-border rounded-md">
      <button
        className="flex items-center gap-2 w-full px-2.5 py-1.5 text-xs hover:bg-muted/30 cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded
          ? <ChevronDown className="h-3 w-3 shrink-0" />
          : <ChevronRight className="h-3 w-3 shrink-0" />
        }
        <Wrench className="h-3 w-3 text-muted-foreground shrink-0" />
        <span className="font-mono font-medium text-foreground">{block.name}</span>
        {block.summary && <span className="font-mono text-muted-foreground truncate">{block.summary}</span>}
      </button>
      {expanded && (
        <div className="px-2.5 pb-2 border-t border-border/30">
          <pre className="text-xs font-mono bg-background rounded p-2 mt-1.5 overflow-x-auto max-h-40 overflow-y-auto">
            {segments.map((seg, i) => (
              <span key={i} className={seg.className}>{seg.text}</span>
            ))}
          </pre>
        </div>
      )}
    </div>
  );
}

function ToolResultBlockView({ block }: { block: ConversationToolResultBlock }) {
  const [expanded, setExpanded] = useState(false);
  const content = block.content;

  const isLong = content.length > 200;
  const isError = block.is_error;

  return (
    <div className={cn("border rounded-md", isError ? "border-destructive/30" : "border-border/50")}>
      <button
        className="flex items-center gap-2 w-full px-2.5 py-1.5 text-xs hover:bg-muted/30 cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded
          ? <ChevronDown className="h-3 w-3 shrink-0" />
          : <ChevronRight className="h-3 w-3 shrink-0" />
        }
        {isError && <AlertCircle className="h-3 w-3 text-destructive shrink-0" />}
        <span className={cn("font-mono text-[11px]", isError ? "text-destructive" : "text-muted-foreground")}>
          Result for {block.tool_use_id.slice(0, 12)}...
        </span>
        <span className="text-[10px] text-muted-foreground/60 ml-auto">
          {content.length.toLocaleString()} chars
        </span>
      </button>
      {(expanded || !isLong) && (
        <div className="px-2.5 pb-2 border-t border-border/30">
          <pre className={cn(
            "text-xs font-mono whitespace-pre-wrap mt-1.5 max-h-60 overflow-y-auto",
            isError && "text-destructive",
          )}>
            {content}
          </pre>
        </div>
      )}
    </div>
  );
}
