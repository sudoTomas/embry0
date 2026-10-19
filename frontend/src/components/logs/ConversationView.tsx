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

interface ConversationViewProps {
  events: LogEvent[];
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

export function ConversationView({ events }: ConversationViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [userScrolledUp, setUserScrolledUp] = useState(false);
  const [turnsShown, setTurnsShown] = useState(INITIAL_TURNS_SHOWN);

  // Group events into turns: turn_start opens one for its run, and the
  // thinking, text, tool_call and tool_result events of that run go into it.
  // A tool_result joins the turn that holds its tool_call.
  const turns = useMemo(() => {
    const result: ConversationTurn[] = [];
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
      return turn;
    };
    const turnFor = (event: LogEvent) => currentByRun.get(event.run_id ?? "") ?? open(event, null);

    for (const event of events) {
      switch (event.type) {
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
    return result;
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
      className="space-y-1 overflow-auto overscroll-contain h-[calc(100dvh-280px)]"
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
        <TurnCard key={turns.length - visibleTurns.length + i} turn={turn} />
      ))}
    </div>
  );
}

function TurnCard({ turn }: { turn: ConversationTurn }) {
  return (
    <div className="space-y-1">
      {turn.divider && (
        <div className="flex items-center gap-2 pt-3">
          <div className="h-px flex-1 bg-border" />
          <span className="text-xs text-muted-foreground font-mono">{turn.divider}</span>
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
            <Bot className="h-3.5 w-3.5 text-primary" />
            <span className="text-xs font-medium text-primary">Assistant</span>
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
  const jsonInput = JSON.stringify(block.input, null, 2);
  const segments = highlightJson(jsonInput);

  return (
    <div className="border border-primary/30 rounded-md">
      <button
        className="flex items-center gap-2 w-full px-2.5 py-1.5 text-xs hover:bg-muted/30 cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        {expanded
          ? <ChevronDown className="h-3 w-3 shrink-0" />
          : <ChevronRight className="h-3 w-3 shrink-0" />
        }
        <Wrench className="h-3 w-3 text-primary shrink-0" />
        <span className="font-mono font-medium text-primary">{block.name}</span>
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
