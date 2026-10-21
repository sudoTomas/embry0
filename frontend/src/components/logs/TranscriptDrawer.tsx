import { useEffect, useRef } from "react";
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
 * The stored-session drawer (spec 3.6): the untruncated messages one agent
 * kept for this job, read from GET /jobs/{id}/agents/{type}/session (redacted
 * server-side). The row is one per job and agent type, merged across the
 * agent's runs, so the drawer describes the agent and the job, not a run.
 * Escape and the close button dismiss it; focus moves to the close button on
 * open and returns to the opener on close (plan 3's Dialog brings the trap).
 */
export function TranscriptDrawer({ open, onClose, jobId, agentType, displayName }: TranscriptDrawerProps) {
  const { data, isLoading, isError, error } = useAgentSession(jobId, agentType, open);
  // The route is operator-tier (docs/api.md): a read key gets 403.
  const forbidden = isError && (error as { response?: { status?: number } } | null)?.response?.status === 403;
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    return () => opener?.focus();
  }, [open]);
  if (!open) return null;
  const title = `Stored session of ${displayName} for this job`;
  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-background/70" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
        className="h-full w-full max-w-3xl overflow-y-auto border-l border-border bg-card p-4 shadow-xl"
      >
        <div className="mb-3 flex items-center gap-2">
          <h2 className="title-pixel text-lg">Stored session</h2>
          <span className="text-sm text-muted-foreground">of {displayName} for this job</span>
          <button ref={closeRef} type="button" onClick={onClose} aria-label="Close" className="ml-auto rounded p-1 hover:bg-muted"><X className="h-4 w-4" /></button>
        </div>
        {isLoading && <p className="text-sm text-muted-foreground">Loading the stored session…</p>}
        {forbidden && <p className="text-sm text-destructive">Operator access is needed to read stored sessions.</p>}
        {isError && !forbidden && <p className="text-sm text-destructive">The stored session could not be loaded.</p>}
        {!isLoading && !isError && !data && <p className="text-sm text-muted-foreground">{`No stored session of ${displayName} for this job.`}</p>}
        {data?.last_turn_at && <p className="mb-2 text-xs text-muted-foreground">Last turn {new Date(data.last_turn_at).toLocaleString()}</p>}
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
          ) : !Array.isArray(m.content) ? null : (
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
