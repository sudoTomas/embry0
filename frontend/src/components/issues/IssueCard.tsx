import { useNavigate } from "react-router";
import { IssuePriorityBadge } from "./IssuePriorityBadge";
import { AgentIndicator } from "./AgentIndicator";
import type { IssueResponse } from "@/lib/types";

interface IssueCardProps {
  issue: IssueResponse;
  draggable?: boolean;
  onDragStart?: (e: React.DragEvent) => void;
}

export function IssueCard({ issue, draggable, onDragStart }: IssueCardProps) {
  const navigate = useNavigate();
  const visibleLabels = issue.labels.slice(0, 2);
  const extraLabels = issue.labels.length - 2;

  const progress = issue.children_count > 0
    ? (issue.children_closed_count / issue.children_count) * 100
    : 0;

  return (
    <div
      className="rounded-lg border border-white/[0.06] bg-background p-3 space-y-2 cursor-pointer hover:border-border-strong hover:bg-overlay transition-colors"
      draggable={draggable}
      onDragStart={onDragStart}
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
              className="inline-flex items-center rounded-md bg-overlay px-1.5 py-0.5 text-xs text-fg-secondary"
            >
              {label}
            </span>
          ))}
          {extraLabels > 0 && (
            <span className="inline-flex items-center rounded-md bg-overlay px-1.5 py-0.5 text-xs text-fg-muted">
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
            <span>{issue.children_closed_count}/{issue.children_count} subtasks</span>
          </div>
          <div className="h-1 w-full rounded-full bg-white/[0.06]">
            <div
              className="h-1 rounded-full bg-fg-secondary transition-all"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>
      )}
    </div>
  );
}
