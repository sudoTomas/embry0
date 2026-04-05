import { useState, useEffect } from "react";
import { Card, CardContent } from "@/components/ui/Card";
import { FilterSelect } from "@/components/ui/FilterSelect";
import { Input } from "@/components/ui/Input";
import { Pagination } from "@/components/ui/Pagination";
import { IssueStatusBadge } from "./IssueStatusBadge";
import { IssuePriorityBadge } from "./IssuePriorityBadge";
import { AgentIndicator } from "./AgentIndicator";
import { formatDate } from "@/lib/utils";
import { useNavigate } from "react-router";
import type { IssueResponse } from "@/lib/types";

const STATUS_OPTIONS = ["open", "triaging", "in_progress", "closed", "cancelled"];
const PRIORITY_OPTIONS = ["critical", "high", "medium", "low"];

interface IssueListViewProps {
  issues: IssueResponse[];
  total: number;
  offset: number;
  limit: number;
  filters: { status?: string; priority?: string; repo?: string };
  repoOptions: string[];
  onStatusChange: (v: string | undefined) => void;
  onPriorityChange: (v: string | undefined) => void;
  onRepoChange: (v: string | undefined) => void;
  onSearchChange: (v: string) => void;
  searchValue: string;
  onPageChange: (offset: number) => void;
}

export function IssueListView({
  issues,
  total,
  offset,
  limit,
  filters,
  repoOptions,
  onStatusChange,
  onPriorityChange,
  onRepoChange,
  onSearchChange,
  searchValue,
  onPageChange,
}: IssueListViewProps) {
  const navigate = useNavigate();
  const [localSearch, setLocalSearch] = useState(searchValue);

  useEffect(() => {
    const timer = setTimeout(() => {
      onSearchChange(localSearch);
    }, 300);
    return () => clearTimeout(timer);
  }, [localSearch, onSearchChange]);

  useEffect(() => {
    setLocalSearch(searchValue);
  }, [searchValue]);

  return (
    <Card>
      <CardContent className="p-0">
        {/* Filter bar */}
        <div className="flex flex-wrap items-center gap-3 border-b border-border px-4 py-3">
          <FilterSelect
            label="Statuses"
            value={filters.status}
            options={STATUS_OPTIONS}
            onChange={onStatusChange}
          />
          <FilterSelect
            label="Priorities"
            value={filters.priority}
            options={PRIORITY_OPTIONS}
            onChange={onPriorityChange}
          />
          <FilterSelect
            label="Repos"
            value={filters.repo}
            options={repoOptions}
            onChange={onRepoChange}
          />
          <Input
            className="h-8 w-48 text-xs"
            placeholder="Search issues..."
            value={localSearch}
            onChange={(e) => setLocalSearch(e.target.value)}
          />
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-white/40">
                <th scope="col" className="text-left px-4 py-3">Status</th>
                <th scope="col" className="text-left px-4 py-3">Title</th>
                <th scope="col" className="text-left px-4 py-3">Priority</th>
                <th scope="col" className="text-left px-4 py-3">Labels</th>
                <th scope="col" className="text-left px-4 py-3">Repo</th>
                <th scope="col" className="text-right px-4 py-3">Updated</th>
                <th scope="col" className="text-right px-4 py-3">Subtasks</th>
                <th scope="col" className="text-right px-4 py-3">Jobs</th>
              </tr>
            </thead>
            <tbody>
              {issues.length === 0 ? (
                <tr>
                  <td colSpan={8}>
                    <div className="flex flex-col items-center justify-center py-16 text-muted-foreground">
                      <span className="text-4xl mb-3">📋</span>
                      <p className="text-sm">No issues found</p>
                    </div>
                  </td>
                </tr>
              ) : (
                issues.map((issue) => {
                  const isDimmed = issue.status === "closed" || issue.status === "cancelled";
                  const visibleLabels = issue.labels.slice(0, 2);
                  const extraLabels = issue.labels.length - 2;

                  return (
                    <tr
                      key={issue.id}
                      className={`border-b border-white/[0.04] hover:bg-cyan-500/[0.02] transition-colors cursor-pointer ${isDimmed ? "opacity-60" : ""}`}
                      onClick={() => navigate(`/issues/${issue.id}`)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          navigate(`/issues/${issue.id}`);
                        }
                      }}
                      tabIndex={0}
                      role="link"
                      aria-label={`View issue: ${issue.title}`}
                    >
                      <td className="px-4 py-3">
                        {issue.active_agent ? (
                          <AgentIndicator agentType={issue.active_agent} />
                        ) : (
                          <IssueStatusBadge status={issue.status} />
                        )}
                      </td>
                      <td className="px-4 py-3 max-w-[280px]">
                        <span className="truncate block">{issue.title}</span>
                        {issue.children_count > 0 && (
                          <span className="text-xs text-blue-400 mt-0.5 block">
                            {issue.children_count} subtask{issue.children_count !== 1 ? "s" : ""}
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <IssuePriorityBadge priority={issue.priority} />
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex flex-wrap gap-1">
                          {visibleLabels.map((label) => (
                            <span
                              key={label}
                              className="inline-flex items-center rounded-md bg-zinc-700/50 px-2 py-0.5 text-xs text-zinc-300"
                            >
                              {label}
                            </span>
                          ))}
                          {extraLabels > 0 && (
                            <span className="inline-flex items-center rounded-md bg-zinc-700/30 px-2 py-0.5 text-xs text-zinc-400">
                              +{extraLabels}
                            </span>
                          )}
                        </div>
                      </td>
                      <td className="px-4 py-3 font-mono text-xs text-muted-foreground">
                        {issue.repo ?? "—"}
                      </td>
                      <td className="px-4 py-3 text-right text-muted-foreground">
                        {formatDate(issue.updated_at)}
                      </td>
                      <td className="px-4 py-3 text-right text-muted-foreground">
                        {issue.children_count > 0 ? issue.children_count : "—"}
                      </td>
                      <td className="px-4 py-3 text-right text-muted-foreground">
                        {issue.jobs_count > 0 ? issue.jobs_count : "—"}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        <Pagination total={total} offset={offset} limit={limit} onPageChange={onPageChange} />
      </CardContent>
    </Card>
  );
}
