import { Dialog } from "@/components/ui/Dialog";
import { useAgentSession } from "@/hooks/useAgentSession";
import { statusOf } from "@/api/jobs";
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
 * Escape, the backdrop and the Close button dismiss it; the focus trap,
 * focus moving in on open and back to the opener on close come from Dialog.
 */
export function TranscriptDrawer({ open, onClose, jobId, agentType, displayName }: TranscriptDrawerProps) {
  const { data, isLoading, isError, error } = useAgentSession(jobId, agentType, open);
  // The route is operator-tier (docs/api.md): a read key gets 403.
  const forbidden = isError && statusOf(error) === 403;
  if (!open) return null;
  const title = `Stored session of ${displayName} for this job`;
  return (
    <Dialog
      open={open}
      onClose={onClose}
      side="right"
      label={title}
      title={
        <>
          <span className="title-page text-lg">Stored session</span>
          <span className="ml-2 text-sm font-normal text-muted-foreground">of {displayName} for this job</span>
        </>
      }
      className="max-w-3xl bg-card"
    >
      <div className="p-4">
        {isLoading && <p className="text-sm text-muted-foreground">Loading the stored session…</p>}
        {forbidden && <p className="text-sm text-destructive">Operator access is needed to read stored sessions.</p>}
        {isError && !forbidden && <p className="text-sm text-destructive">The stored session could not be loaded.</p>}
        {!isLoading && !isError && !data && <p className="text-sm text-muted-foreground">{`No stored session of ${displayName} for this job.`}</p>}
        {data?.last_turn_at && <p className="mb-2 text-xs text-muted-foreground">Last turn {new Date(data.last_turn_at).toLocaleString()}</p>}
        {data && <Messages session={data} />}
      </div>
    </Dialog>
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
