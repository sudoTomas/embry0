import { ChevronDown, Copy, MessageSquare, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/Button";
import type { ChatDock } from "@/hooks/useWorldChatDock";
import { cn } from "@/lib/utils";
import { debugText, type NarrationLine } from "@/lib/world";

/** How many lines the activity box keeps. */
export const CHAT_LINES = 6;

const TONE: Record<NarrationLine["tone"], string> = {
  info: "text-fg-secondary",
  win: "text-success",
  loss: "text-destructive",
  alert: "text-warning",
};

async function copy(text: string, done: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast.success(done);
  } catch {
    // An insecure origin has no clipboard: hand the text over instead.
    toast.error("Could not copy. Here it is:", { description: text });
  }
}

interface WorldChatProps {
  lines: readonly NarrationLine[];
  dock: ChatDock;
  onDock: (next: ChatDock) => void;
}

/**
 * The activity box, bottom left: what the parties did since the page opened,
 * newest last, in the game's words. Every line copies as a debug block (the
 * time, the line, the job, its error code and exact message for a failure,
 * its page), and Copy all takes the lines shown. It folds to a tab and
 * closes; the page header opens it again.
 */
export function WorldChat({ lines, dock, onDock }: WorldChatProps) {
  const shown = lines.slice(-CHAT_LINES);
  if (dock === "closed") return null;
  if (dock === "collapsed") {
    return (
      <Button variant="outline" size="sm" className="bg-surface shadow-lg" onClick={() => onDock("open")} data-testid="world-chat-tab">
        <MessageSquare className="h-3.5 w-3.5" />
        Activity{lines.length ? ` (${shown.length})` : ""}
      </Button>
    );
  }
  return (
    <section
      className="w-full max-w-md rounded-lg border border-border bg-surface/95 shadow-lg backdrop-blur-sm"
      aria-labelledby="world-chat-title"
      data-testid="world-chat"
    >
      <header className="flex items-center gap-1 border-b border-border py-1 pl-3 pr-1">
        <h2 id="world-chat-title" className="flex-1 text-xs font-semibold text-fg-secondary">
          Activity
        </h2>
        <Button
          variant="ghost"
          size="xs"
          disabled={!shown.length}
          onClick={() => void copy(shown.map(debugText).join("\n\n"), `Copied ${shown.length} line${shown.length === 1 ? "" : "s"}`)}
        >
          Copy all
        </Button>
        <Button variant="ghost" size="icon-sm" aria-label="Collapse activity" title="Collapse" onClick={() => onDock("collapsed")}>
          <ChevronDown className="h-4 w-4" />
        </Button>
        <Button variant="ghost" size="icon-sm" aria-label="Close activity" title="Close" onClick={() => onDock("closed")}>
          <X className="h-4 w-4" />
        </Button>
      </header>
      <div className="max-h-48 overflow-y-auto px-3 py-2 text-xs" aria-live="polite">
        {shown.length === 0 ? (
          <p className="text-fg-muted">Parties will report here.</p>
        ) : (
          <ol className="space-y-1">
            {shown.map((l) => (
              <li key={`${l.key}@${l.at}`} data-tone={l.tone} className="group flex items-start gap-2" data-testid="world-chat-line">
                <span className={cn("min-w-0 flex-1 select-text", TONE[l.tone])}>{l.text}</span>
                <button
                  type="button"
                  aria-label="Copy this line for debugging"
                  title="Copy for debugging"
                  className="shrink-0 rounded p-0.5 text-fg-muted opacity-60 transition-opacity hover:bg-overlay hover:text-fg focus-visible:opacity-100 group-hover:opacity-100"
                  onClick={() => void copy(debugText(l), "Line copied")}
                >
                  <Copy className="h-3.5 w-3.5" />
                </button>
              </li>
            ))}
          </ol>
        )}
      </div>
    </section>
  );
}
