import { RESULT_COLORS, TIER_COLORS } from "@/lib/constants";
import { formatCost, formatDate } from "@/lib/utils";
import type { TraceResponse } from "@/lib/types";
import {
  X,
  Wrench,
  ShieldCheck,
  ArrowUpRight,
  Terminal,
  AlertTriangle,
} from "lucide-react";

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
  const toolEntries = Object.entries(trace.tools_called).sort(
    ([, a], [, b]) => b - a
  );
  const totalToolCalls = toolEntries.reduce((sum, [, count]) => sum + count, 0);

  return (
    <tr>
      <td colSpan={10} className="p-0">
        <div className="border-t border-primary/20 bg-muted/20 px-6 py-5">
          {/* Header */}
          <div className="flex items-start justify-between mb-5">
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-3">
                <span className={`text-sm font-semibold capitalize ${RESULT_COLORS[trace.result]}`}>
                  {trace.result}
                </span>
                <span className="text-sm capitalize">{trace.role}</span>
                <span className={`text-xs capitalize ${TIER_COLORS[trace.tier]}`}>
                  {trace.tier}
                </span>
                <span className="text-xs text-muted-foreground">
                  attempt {trace.attempt}
                </span>
              </div>
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <span className="font-mono">{trace.model}</span>
                <span>via</span>
                <span className="font-mono">{trace.provider_mode}</span>
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
                        <th className="text-right px-3 py-1.5">Errors</th>
                      </tr>
                    </thead>
                    <tbody>
                      {toolEntries.map(([tool, count]) => {
                        const errorCount = trace.tool_errors[tool] ?? 0;
                        return (
                          <tr
                            key={tool}
                            className="border-b border-border/50 last:border-b-0"
                          >
                            <td className="px-3 py-1.5 font-mono text-xs">
                              {tool}
                            </td>
                            <td className="px-3 py-1.5 text-right tabular-nums">
                              {count}
                            </td>
                            <td
                              className={`px-3 py-1.5 text-right tabular-nums ${
                                errorCount > 0 ? "text-destructive font-medium" : "text-muted-foreground"
                              }`}
                            >
                              {errorCount > 0 ? errorCount : "--"}
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">No tool calls recorded</p>
              )}
            </div>

            {/* Right column: Validation, Escalation, Session */}
            <div className="space-y-5">
              {/* Validation */}
              {trace.validation && (
                <div>
                  <SectionLabel>
                    <span className="inline-flex items-center gap-1.5">
                      <ShieldCheck className="h-3.5 w-3.5" />
                      Validation
                    </span>
                  </SectionLabel>
                  <div className="rounded-md border border-border bg-background p-3 space-y-2">
                    <div className="flex items-center gap-2">
                      <span
                        className={`text-sm font-medium ${
                          trace.validation.passed ? "text-success" : "text-destructive"
                        }`}
                      >
                        {trace.validation.passed ? "Passed" : "Failed"}
                      </span>
                      <span className="text-xs text-muted-foreground capitalize">
                        ({trace.validation.category.replaceAll("_", " ")})
                      </span>
                    </div>
                    <p className="text-sm text-muted-foreground">
                      {trace.validation.summary}
                    </p>
                  </div>
                </div>
              )}

              {/* Escalation */}
              {trace.escalated_from && (
                <div>
                  <SectionLabel>
                    <span className="inline-flex items-center gap-1.5">
                      <ArrowUpRight className="h-3.5 w-3.5" />
                      Escalation
                    </span>
                  </SectionLabel>
                  <div className="rounded-md border border-warning/30 bg-warning/5 p-3 space-y-1">
                    <p className="text-sm">
                      Escalated from{" "}
                      <span className="font-mono text-xs font-medium">
                        {trace.escalated_from}
                      </span>
                    </p>
                    {trace.escalation_reason && (
                      <p className="text-xs text-muted-foreground">
                        {trace.escalation_reason}
                      </p>
                    )}
                  </div>
                </div>
              )}

              {/* Session Info */}
              <div>
                <SectionLabel>
                  <span className="inline-flex items-center gap-1.5">
                    <Terminal className="h-3.5 w-3.5" />
                    Session
                  </span>
                </SectionLabel>
                <dl className="grid grid-cols-2 gap-3">
                  <InfoItem label="Session ID">
                    {trace.session_id ? (
                      <span className="font-mono text-xs" title={trace.session_id}>
                        {trace.session_id.length > 16
                          ? `${trace.session_id.slice(0, 16)}...`
                          : trace.session_id}
                      </span>
                    ) : (
                      <span className="text-muted-foreground">--</span>
                    )}
                  </InfoItem>
                  <InfoItem label="Turns">
                    {trace.turns_used ?? <span className="text-muted-foreground">--</span>}
                  </InfoItem>
                  <InfoItem label="Tokens In">
                    {trace.tokens_input != null ? (
                      <span className="tabular-nums">
                        {trace.tokens_input.toLocaleString()}
                      </span>
                    ) : (
                      <span className="text-muted-foreground">--</span>
                    )}
                  </InfoItem>
                  <InfoItem label="Tokens Out">
                    {trace.tokens_output != null ? (
                      <span className="tabular-nums">
                        {trace.tokens_output.toLocaleString()}
                      </span>
                    ) : (
                      <span className="text-muted-foreground">--</span>
                    )}
                  </InfoItem>
                  <InfoItem label="Duration">
                    {trace.duration_seconds != null
                      ? `${trace.duration_seconds.toFixed(0)}s`
                      : "--"}
                  </InfoItem>
                  <InfoItem label="Cost">
                    {trace.cost_usd != null ? formatCost(trace.cost_usd) : "--"}
                  </InfoItem>
                  <InfoItem label="Stop Reason">
                    <span className="font-mono text-xs">
                      {trace.stop_reason ?? <span className="text-muted-foreground">--</span>}
                    </span>
                  </InfoItem>
                  <InfoItem label="Timestamp">
                    {formatDate(trace.timestamp)}
                  </InfoItem>
                </dl>
              </div>

              {/* Error */}
              {trace.error_message && (
                <div>
                  <SectionLabel>
                    <span className="inline-flex items-center gap-1.5 text-destructive">
                      <AlertTriangle className="h-3.5 w-3.5" />
                      Error
                    </span>
                  </SectionLabel>
                  <pre className="text-xs text-destructive whitespace-pre-wrap font-mono bg-destructive/10 border border-destructive/30 rounded-md p-3">
                    {trace.error_message}
                  </pre>
                </div>
              )}
            </div>
          </div>
        </div>
      </td>
    </tr>
  );
}
