import { useNavigate } from "react-router";
import { IssuePriorityBadge } from "./IssuePriorityBadge";
import { AgentIndicator } from "./AgentIndicator";
import type { IssueResponse } from "@/lib/types";

interface IssueCardProps {
  issue: IssueResponse;
}

export function IssueCard({ issue }: IssueCardProps) {
  const navigate = useNavigate();
  const visibleLabels = issue.labels.slice(0, 2);
  const extraLabels = issue.labels.length - 2;

  const subtaskDone = issue.children_count > 0
    ? Math.min(issue.jobs_count, issue.children_count)
    : 0;
  const subtaskProgress = issue.children_count > 0
    ? subtaskDone / issue.children_count
    : 0;

  return (
    <div
      className="rounded-lg border border-white/[0.06] bg-background p-3 space-y-2 cursor-pointer hover:border-cyan-500/30 hover:bg-cyan-500/[0.02] transition-colors"
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
      {/* Header row: priority + agent indicator */}
      <div className="flex items-center justify-between gap-2">
        <IssuePriorityBadge priority={issue.priority} />
        {issue.active_agent && (
          <AgentIndicator agentType={issue.active_agent} size="sm" />
        )}
      </div>

      {/* Title */}
      <p className="text-sm font-medium leading-snug line-clamp-2">{issue.title}</p>

      {/* Labels */}
      {issue.labels.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {visibleLabels.map((label) => (
            <span
              key={label}
              className="inline-flex items-center rounded-md bg-zinc-700/50 px-1.5 py-0.5 text-xs text-zinc-300"
            >
              {label}
            </span>
          ))}
          {extraLabels > 0 && (
            <span className="inline-flex items-center rounded-md bg-zinc-700/30 px-1.5 py-0.5 text-xs text-zinc-400">
              +{extraLabels}
            </span>
          )}
        </div>
      )}

      {/* Repo */}
      {issue.repo && (
        <p className="text-xs font-mono text-muted-foreground truncate">{issue.repo}</p>
      )}

      {/* Subtask progress bar */}
      {issue.children_count > 0 && (
        <div className="space-y-1">
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>{subtaskDone}/{issue.children_count} subtasks</span>
          </div>
          <div className="h-1 w-full rounded-full bg-white/[0.06]">
            <div
              className="h-1 rounded-full bg-blue-400 transition-all"
              style={{ width: `${subtaskProgress * 100}%` }}
            />
          </div>
        </div>
      )}
    </div>
  );
}
