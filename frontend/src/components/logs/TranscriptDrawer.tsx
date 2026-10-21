import { useEffect } from "react";
import { X } from "lucide-react";
import { useAgentSession } from "@/hooks/useAgentSession";
import type { AgentSession } from "@/lib/types";

interface TranscriptDrawerProps {
  open: boolean;
  onClose: () => void;
  jobId: string;
  agentType: string | null;
  displayName: string;
}

/**
 * The full-transcript drawer (spec 3.6): the untruncated messages of one
 * completed run, read from GET /jobs/{id}/agents/{type}/session (redacted
 * server-side). Escape and the close button dismiss it.
 */
export function TranscriptDrawer({ open, onClose, jobId, agentType, displayName }: TranscriptDrawerProps) {
  const { data, isLoading, isError } = useAgentSession(jobId, agentType, open);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-background/70" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={`Full transcript: ${displayName}`}
        onClick={(e) => e.stopPropagation()}
        className="h-full w-full max-w-3xl overflow-y-auto border-l border-border bg-card p-4 shadow-xl"
      >
        <div className="mb-3 flex items-center gap-2">
          <h2 className="title-pixel text-lg">Full transcript</h2>
          <span className="text-sm text-muted-foreground">{displayName}</span>
          <button type="button" onClick={onClose} aria-label="Close" className="ml-auto rounded p-1 hover:bg-muted"><X className="h-4 w-4" /></button>
        </div>
        {isLoading && <p className="text-sm text-muted-foreground">Loading transcript…</p>}
        {isError && <p className="text-sm text-destructive">The transcript could not be loaded.</p>}
        {!isLoading && !isError && !data && <p className="text-sm text-muted-foreground">No stored transcript for this run.</p>}
        {data && <Messages session={data} />}
      </div>
    </div>
  );
}

function Messages({ session }: { session: AgentSession }) {
  return (
    <ol className="space-y-3 text-sm">
      {session.messages.map((m, i) => (
        <li key={i} className="rounded border border-border p-2">
          <div className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">{m.role}</div>
          {typeof m.content === "string" ? (
            <pre className="whitespace-pre-wrap font-sans">{m.content}</pre>
          ) : (
            <div className="space-y-1">
              {m.content.map((block, j) => (
                <div key={j}>
                  {block.type === "text" && <pre className="whitespace-pre-wrap font-sans">{block.text}</pre>}
                  {block.type === "tool_use" && (
                    <details><summary className="cursor-pointer font-mono text-xs">tool_use · {block.name}</summary><pre className="mt-1 overflow-x-auto text-xs">{JSON.stringify(block.input, null, 2)}</pre></details>
                  )}
                  {block.type === "tool_result" && (
                    <details><summary className="cursor-pointer font-mono text-xs">tool_result</summary><pre className="mt-1 overflow-x-auto whitespace-pre-wrap text-xs">{typeof block.content === "string" ? block.content : JSON.stringify(block.content, null, 2)}</pre></details>
                  )}
                  {block.type !== "text" && block.type !== "tool_use" && block.type !== "tool_result" && (
                    <pre className="overflow-x-auto text-xs text-muted-foreground">{JSON.stringify(block)}</pre>
                  )}
                </div>
              ))}
            </div>
          )}
        </li>
      ))}
    </ol>
  );
}
