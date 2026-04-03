import { useState } from "react";
import { Card, CardContent } from "@/components/ui/Card";
import { FilterSelect } from "@/components/ui/FilterSelect";
import { Pagination } from "@/components/ui/Pagination";
import { RESULT_COLORS, TIER_COLORS } from "@/lib/constants";
import { formatCost, formatDate } from "@/lib/utils";
import { ChevronDown } from "lucide-react";
import { TraceDetailPanel } from "@/components/traces/TraceDetailPanel";
import type { TraceResponse } from "@/lib/types";

interface TracesTableProps {
  traces: TraceResponse[];
  total: number;
  offset: number;
  limit: number;
  filters: { role?: string; result?: string; tier?: string };
  onFilterChange: (f: { role?: string; result?: string; tier?: string }) => void;
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
            label="Role"
            value={filters.role}
            options={["developer", "validator"]}
            onChange={(role) => onFilterChange({ role })}
          />
          <FilterSelect
            label="Result"
            value={filters.result}
            options={["pass", "fail", "partial", "error", "timeout", "budget_exceeded"]}
            onChange={(result) => onFilterChange({ result })}
          />
          <FilterSelect
            label="Tier"
            value={filters.tier}
            options={["routine", "standard", "complex"]}
            onChange={(tier) => onFilterChange({ tier })}
          />
        </div>

        {/* Table */}
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-white/40">
                <th scope="col" className="w-8 px-2 py-3" />
                <th scope="col" className="text-left px-4 py-3">Issue #</th>
                <th scope="col" className="text-left px-4 py-3">Repo</th>
                <th scope="col" className="text-left px-4 py-3">Role</th>
                <th scope="col" className="text-left px-4 py-3">Tier</th>
                <th scope="col" className="text-left px-4 py-3">Result</th>
                <th scope="col" className="text-right px-4 py-3">Cost</th>
                <th scope="col" className="text-right px-4 py-3">Duration</th>
                <th scope="col" className="text-right px-4 py-3">Turns</th>
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
  return (
    <>
      <tr
        className={`border-b border-white/[0.04] cursor-pointer transition-colors ${
          isSelected
            ? "bg-primary/5 border-l-2 border-l-primary"
            : "hover:bg-cyan-500/[0.02]"
        }`}
        onClick={onClick}
        onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onClick(); } }}
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
        <td className="px-4 py-3">{trace.issue_number}</td>
        <td className="px-4 py-3 font-mono text-xs">{trace.repo}</td>
        <td className="px-4 py-3 capitalize">{trace.role}</td>
        <td className={`px-4 py-3 capitalize ${TIER_COLORS[trace.tier]}`}>{trace.tier}</td>
        <td className={`px-4 py-3 capitalize ${RESULT_COLORS[trace.result]}`}>{trace.result}</td>
        <td className="px-4 py-3 text-right">{trace.cost_usd != null ? formatCost(trace.cost_usd) : "\u2014"}</td>
        <td className="px-4 py-3 text-right">
          {trace.duration_seconds != null ? `${trace.duration_seconds.toFixed(0)}s` : "\u2014"}
        </td>
        <td className="px-4 py-3 text-right">{trace.turns_used ?? "\u2014"}</td>
        <td className="px-4 py-3 text-right text-muted-foreground">{formatDate(trace.timestamp)}</td>
      </tr>
      {isSelected && (
        <TraceDetailPanel trace={trace} onClose={onClose} />
      )}
    </>
  );
}
