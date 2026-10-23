import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { Card, CardContent } from "@/components/ui/Card";
import { FilterSelect } from "@/components/ui/FilterSelect";
import { Pagination } from "@/components/ui/Pagination";
import { RESULT_COLORS } from "@/lib/constants";
import { formatCost, formatDateTime, formatTokens } from "@/lib/utils";
import { TraceDetailPanel } from "@/components/traces/TraceDetailPanel";
import { useAgents } from "@/hooks/useAgents";
import type { TraceResponse } from "@/lib/types";

interface TracesTableFilters {
  agent_type?: string;
  result?: string;
}

/**
 * The table's columns, in order. The head renders from this list, and the
 * rows that run the full width (a trace's detail, the empty state) span
 * its length, so a column added here widens them too.
 */
const COLUMNS: { label: string; className?: string }[] = [
  { label: "", className: "w-8" },
  { label: "Trace" },
  { label: "Agent" },
  { label: "Model" },
  { label: "Result" },
  { label: "Cost", className: "text-right" },
  { label: "Tokens", className: "text-right" },
  { label: "Duration", className: "text-right" },
  { label: "Tools", className: "text-right" },
  { label: "Timestamp", className: "text-right" },
];

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
  const { data: agents } = useAgents();
  const agentOptions = (agents ?? []).map((a) => a.type);

  function handleRowClick(traceId: string) {
    setSelectedTraceId((prev) => (prev === traceId ? null : traceId));
  }

  return (
    <Card>
      <CardContent className="p-0">
        {/* Filters */}
        <div className="flex items-center gap-3 border-b border-border px-4 py-3">
          <FilterSelect
            label="Agents"
            value={filters.agent_type}
            options={agentOptions}
            onChange={(agent_type) => onFilterChange({ ...filters, agent_type })}
          />
          <FilterSelect
            label="Results"
            value={filters.result}
            options={["success", "error", "running"]}
            onChange={(result) => onFilterChange({ ...filters, result })}
          />
        </div>

        {/* Table. A size container, so an open trace's detail can be as wide
            as the part of the table in view (TraceDetailPanel). */}
        <div className="@container overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                {COLUMNS.map((col, i) => (
                  <th key={i} scope="col" className={col.className}>
                    {col.label}
                  </th>
                ))}
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
                  <td colSpan={COLUMNS.length} className="py-8 text-center text-muted-foreground">
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
            ? "bg-overlay border-l-2 border-l-border"
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
        <td className="text-muted-foreground">
          <ChevronDown
            className={`h-4 w-4 transition-transform ${isSelected ? "rotate-0" : "-rotate-90"}`}
          />
        </td>
        <td className="font-mono text-xs" title={trace.trace_id}>
          {trace.trace_id.length > 20 ? `${trace.trace_id.slice(0, 20)}...` : trace.trace_id}
        </td>
        <td className="capitalize">{trace.agent_type}</td>
        <td className="font-mono text-xs">{trace.model}</td>
        <td className={`capitalize ${RESULT_COLORS[trace.result] ?? ""}`}>
          {trace.result}
        </td>
        <td className="text-right tabular-nums">
          {trace.cost_usd != null ? formatCost(trace.cost_usd) : "\u2014"}
        </td>
        <td
          className="text-right tabular-nums"
          title={`in ${trace.input_tokens ?? 0} / out ${trace.output_tokens ?? 0} / cache read ${trace.cache_read_tokens ?? 0}`}
        >
          {(trace.input_tokens ?? 0) + (trace.output_tokens ?? 0) > 0
            ? `${formatTokens(trace.input_tokens)} / ${formatTokens(trace.output_tokens)}`
            : "\u2014"}
        </td>
        <td className="text-right tabular-nums">
          {durationSeconds != null ? `${durationSeconds.toFixed(1)}s` : "\u2014"}
        </td>
        <td className="text-right tabular-nums">{toolCount || "\u2014"}</td>
        <td className="text-right text-muted-foreground">
          {formatDateTime(trace.created_at)}
        </td>
      </tr>
      {isSelected && <TraceDetailPanel trace={trace} colSpan={COLUMNS.length} onClose={onClose} />}
    </>
  );
}
