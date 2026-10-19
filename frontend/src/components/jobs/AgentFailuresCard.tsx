import type { AgentFailure } from "@/lib/types";

/**
 * #281: why each errored agent run failed — the runner's code plus the redacted
 * stderr tail the server kept. Collapsed by default: a stderr tail is operator
 * detail, and a run that was later recovered shows up on completed jobs too.
 */
export function AgentFailuresCard({ failures }: { failures?: AgentFailure[] | null }) {
  if (!failures || failures.length === 0) return null;
  return (
    <details
      data-testid="agent-failures"
      className="px-4 py-3 rounded-lg border border-white/10 bg-white/[0.02]"
    >
      <summary className="text-sm text-white/70 cursor-pointer">
        Agent run failures ({failures.length})
      </summary>
      <div className="mt-3 space-y-3">
        {failures.map((f) => (
          <div key={f.trace_id} data-testid="agent-failure">
            <div className="text-xs text-white/60">
              <span className="font-mono">{f.agent_type}</span>
              {f.error_code && (
                <span className="ml-2 font-mono text-destructive/70">{f.error_code}</span>
              )}
            </div>
            {f.result_summary && (
              <div className="text-xs text-white/40 mt-0.5">{f.result_summary}</div>
            )}
            {f.stderr_tail && (
              <pre className="mt-1 max-h-64 overflow-auto rounded bg-black/40 p-2 text-[11px] leading-snug text-white/60 whitespace-pre-wrap break-words">
                {f.stderr_tail}
              </pre>
            )}
          </div>
        ))}
      </div>
    </details>
  );
}
