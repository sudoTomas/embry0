import { X, Wrench, Terminal, FileText } from "lucide-react";
import { RESULT_COLORS } from "@/lib/constants";
import { formatCost, formatDate, formatTokens } from "@/lib/utils";
import type { TraceResponse } from "@/lib/types";

interface TraceDetailPanelProps {
  trace: TraceResponse;
  onClose: () => void;
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground mb-2">
      {children}
    </h3>
  );
}

function InfoItem({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

export function TraceDetailPanel({ trace, onClose }: TraceDetailPanelProps) {
  const toolEntries = Object.entries(trace.tools_called ?? {}).sort(
    ([, a], [, b]) => b - a,
  );
  const totalToolCalls = toolEntries.reduce((sum, [, count]) => sum + count, 0);
  const durationSeconds = trace.duration_ms > 0 ? trace.duration_ms / 1000 : null;

  return (
    <tr>
      <td colSpan={9} className="p-0">
        <div className="border-t border-primary/20 bg-muted/20 px-6 py-5">
          {/* Header */}
          <div className="flex items-start justify-between mb-5">
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-3">
                <span className={`text-sm font-semibold capitalize ${RESULT_COLORS[trace.result] ?? ""}`}>
                  {trace.result}
                </span>
                <span className="text-sm capitalize">{trace.agent_type}</span>
              </div>
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <span className="font-mono">{trace.model}</span>
              </div>
              <span className="text-xs text-muted-foreground font-mono">
                {trace.trace_id}
              </span>
            </div>
            <button
              onClick={(e) => {
                e.stopPropagation();
                onClose();
              }}
              className="p-1 rounded-md hover:bg-muted text-muted-foreground hover:text-foreground transition-colors"
              aria-label="Close detail panel"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Left column: Tools Called */}
            <div>
              <SectionLabel>
                <span className="inline-flex items-center gap-1.5">
                  <Wrench className="h-3.5 w-3.5" />
                  Tools Called ({totalToolCalls} total)
                </span>
              </SectionLabel>
              {toolEntries.length > 0 ? (
                <div className="rounded-md border border-border bg-background overflow-hidden">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b border-border text-xs text-muted-foreground">
                        <th className="text-left px-3 py-1.5">Tool</th>
                        <th className="text-right px-3 py-1.5">Calls</th>
                      </tr>
                    </thead>
                    <tbody>
                      {toolEntries.map(([tool, count]) => (
                        <tr
                          key={tool}
                          className="border-b border-border/50 last:border-b-0"
                        >
                          <td className="px-3 py-1.5 font-mono text-xs">{tool}</td>
                          <td className="px-3 py-1.5 text-right tabular-nums">{count}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">No tool calls recorded</p>
              )}
            </div>

            {/* Right column: Session info + summary */}
            <div className="space-y-5">
              {/* Session Info */}
              <div>
                <SectionLabel>
                  <span className="inline-flex items-center gap-1.5">
                    <Terminal className="h-3.5 w-3.5" />
                    Execution
                  </span>
                </SectionLabel>
                <dl className="grid grid-cols-2 gap-3">
                  <InfoItem label="Job ID">
                    <span className="font-mono text-xs" title={trace.job_id}>
                      {trace.job_id.length > 16
                        ? `${trace.job_id.slice(0, 16)}...`
                        : trace.job_id}
                    </span>
                  </InfoItem>
                  <InfoItem label="Agent">
                    <span className="capitalize">{trace.agent_type}</span>
                  </InfoItem>
                  <InfoItem label="Duration">
                    {durationSeconds != null ? `${durationSeconds.toFixed(1)}s` : "--"}
                  </InfoItem>
                  <InfoItem label="Cost">
                    {trace.cost_usd != null ? formatCost(trace.cost_usd) : "--"}
                  </InfoItem>
                  <InfoItem label="Model">
                    <span className="font-mono text-xs">{trace.model}</span>
                  </InfoItem>
                  <InfoItem label="Created">{formatDate(trace.created_at)}</InfoItem>
                  <InfoItem label="Tokens in / out">
                    <span className="tabular-nums">
                      {formatTokens(trace.input_tokens ?? 0)} /{" "}
                      {formatTokens(trace.output_tokens ?? 0)}
                    </span>
                  </InfoItem>
                  <InfoItem label="Cache read / write">
                    <span className="tabular-nums">
                      {formatTokens(trace.cache_read_tokens ?? 0)} /{" "}
                      {formatTokens(trace.cache_creation_tokens ?? 0)}
                    </span>
                  </InfoItem>
                </dl>
              </div>

              {/* Result summary */}
              {trace.result_summary && (
                <div>
                  <SectionLabel>
                    <span className="inline-flex items-center gap-1.5">
                      <FileText className="h-3.5 w-3.5" />
                      Summary
                    </span>
                  </SectionLabel>
                  <p className="text-sm text-muted-foreground whitespace-pre-wrap">
                    {trace.result_summary}
                  </p>
                </div>
              )}
            </div>
          </div>
        </div>
      </td>
    </tr>
  );
}
