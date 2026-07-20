import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { Card, CardContent } from "@/components/ui/Card";
import { FilterSelect } from "@/components/ui/FilterSelect";
import { Pagination } from "@/components/ui/Pagination";
import { RESULT_COLORS } from "@/lib/constants";
import { formatCost, formatDate, formatTokens } from "@/lib/utils";
import { TraceDetailPanel } from "@/components/traces/TraceDetailPanel";
import type { TraceResponse } from "@/lib/types";

interface TracesTableFilters {
  agent_type?: string;
  result?: string;
}

interface TracesTableProps {
  traces: TraceResponse[];
  total: number;
  offset: number;
  limit: number;
  filters: TracesTableFilters;
  onFilterChange: (f: TracesTableFilters) => void;
  onPageChange: (offset: number) => void;
}

export function TracesTable({
  traces,
  total,
  offset,
  limit,
  filters,
  onFilterChange,
  onPageChange,
}: TracesTableProps) {
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null);

  function handleRowClick(traceId: string) {
    setSelectedTraceId((prev) => (prev === traceId ? null : traceId));
  }

  return (
    <Card>
      <CardContent className="p-0">
        {/* Filters */}
        <div className="flex items-center gap-3 border-b border-border px-4 py-3">
          <FilterSelect
            label="Agent"
            value={filters.agent_type}
            options={["triage", "developer", "review", "explorer"]}
            onChange={(agent_type) => onFilterChange({ ...filters, agent_type })}
          />
          <FilterSelect
            label="Result"
            value={filters.result}
            options={["pass", "fail", "partial", "error", "timeout", "budget_exceeded"]}
            onChange={(result) => onFilterChange({ ...filters, result })}
          />
        </div>

        {/* Table */}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-muted-foreground">
                <th scope="col" className="w-8 px-2 py-3" />
                <th scope="col" className="text-left px-4 py-3">Trace</th>
                <th scope="col" className="text-left px-4 py-3">Agent</th>
                <th scope="col" className="text-left px-4 py-3">Model</th>
                <th scope="col" className="text-left px-4 py-3">Result</th>
                <th scope="col" className="text-right px-4 py-3">Cost</th>
                <th scope="col" className="text-right px-4 py-3">Tokens</th>
                <th scope="col" className="text-right px-4 py-3">Duration</th>
                <th scope="col" className="text-right px-4 py-3">Tools</th>
                <th scope="col" className="text-right px-4 py-3">Timestamp</th>
              </tr>
            </thead>
            <tbody>
              {traces.map((t) => {
                const isSelected = selectedTraceId === t.trace_id;
                return (
                  <TraceRow
                    key={t.trace_id}
                    trace={t}
                    isSelected={isSelected}
                    onClick={() => handleRowClick(t.trace_id)}
                    onClose={() => setSelectedTraceId(null)}
                  />
                );
              })}
              {traces.length === 0 && (
                <tr>
                  <td colSpan={10} className="px-4 py-8 text-center text-muted-foreground">
                    No traces found
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination */}
        <Pagination total={total} offset={offset} limit={limit} onPageChange={onPageChange} />
      </CardContent>
    </Card>
  );
}

function TraceRow({
  trace,
  isSelected,
  onClick,
  onClose,
}: {
  trace: TraceResponse;
  isSelected: boolean;
  onClick: () => void;
  onClose: () => void;
}) {
  const toolCount = Object.values(trace.tools_called ?? {}).reduce((sum, n) => sum + n, 0);
  const durationSeconds = trace.duration_ms > 0 ? trace.duration_ms / 1000 : null;

  return (
    <>
      <tr
        className={`border-b border-border/50 cursor-pointer transition-colors ${
          isSelected
            ? "bg-primary/5 border-l-2 border-l-primary"
            : "hover:bg-muted/30"
        }`}
        onClick={onClick}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            onClick();
          }
        }}
        tabIndex={0}
        role="button"
        aria-label={`View trace ${trace.trace_id}`}
        aria-expanded={isSelected}
      >
        <td className="px-2 py-3 text-muted-foreground">
          <ChevronDown
            className={`h-4 w-4 transition-transform ${isSelected ? "rotate-0" : "-rotate-90"}`}
          />
        </td>
        <td className="px-4 py-3 font-mono text-xs" title={trace.trace_id}>
          {trace.trace_id.length > 20 ? `${trace.trace_id.slice(0, 20)}...` : trace.trace_id}
        </td>
        <td className="px-4 py-3 capitalize">{trace.agent_type}</td>
        <td className="px-4 py-3 font-mono text-xs">{trace.model}</td>
        <td className={`px-4 py-3 capitalize ${RESULT_COLORS[trace.result] ?? ""}`}>
          {trace.result}
        </td>
        <td className="px-4 py-3 text-right tabular-nums">
          {trace.cost_usd != null ? formatCost(trace.cost_usd) : "\u2014"}
        </td>
        <td
          className="px-4 py-3 text-right tabular-nums"
          title={`in ${trace.input_tokens ?? 0} / out ${trace.output_tokens ?? 0} / cache read ${trace.cache_read_tokens ?? 0}`}
        >
          {(trace.input_tokens ?? 0) + (trace.output_tokens ?? 0) > 0
            ? `${formatTokens(trace.input_tokens)} / ${formatTokens(trace.output_tokens)}`
            : "\u2014"}
        </td>
        <td className="px-4 py-3 text-right tabular-nums">
          {durationSeconds != null ? `${durationSeconds.toFixed(1)}s` : "\u2014"}
        </td>
        <td className="px-4 py-3 text-right tabular-nums">{toolCount || "\u2014"}</td>
        <td className="px-4 py-3 text-right text-muted-foreground">
          {formatDate(trace.created_at)}
        </td>
      </tr>
      {isSelected && <TraceDetailPanel trace={trace} onClose={onClose} />}
    </>
  );
}
