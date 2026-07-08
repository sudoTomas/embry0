import type { CSSProperties, ReactNode } from "react";
import { useNavigate } from "react-router";
import { cn } from "@/lib/utils";
import type { JobResponse } from "@/lib/types";

interface ConsoleCardShellProps {
  job: JobResponse;
  className?: string;
  /** Inline style override — NeedsYouCard passes the amber-glow treatment. */
  style?: CSSProperties;
  /** Right-aligned header content (live dot, status sub-badge). */
  headerRight?: ReactNode;
  children?: ReactNode;
}

/**
 * Shared chrome for every board card: repo + 8-char mono job id, first line
 * of the task as the title, an issue chip when the job originated from an
 * issue, and click-anywhere navigation to JobDetailPage (JobsTable's
 * row-as-link idiom — inner interactive elements must stopPropagation).
 *
 * `issue_id`/`issue_number` both null ⇒ operator-dispatched job — the task
 * text IS the title, no issue chip, no issue assumed.
 */
export function ConsoleCardShell({ job, className, style, headerRight, children }: ConsoleCardShellProps) {
  const navigate = useNavigate();
  const hasIssue = job.issue_id != null || job.issue_number != null;
  const title = job.task.split("\n")[0];

  return (
    <div
      onClick={() => navigate(`/jobs/${job.job_id}`)}
      onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); navigate(`/jobs/${job.job_id}`); } }}
      tabIndex={0}
      role="link"
      aria-label={`View job ${job.job_id}`}
      className={cn(
        "rounded-lg border border-white/[0.06] bg-white/[0.02] p-3 cursor-pointer transition-colors hover:bg-white/[0.04]",
        className,
      )}
      style={style}
    >
      <div className="flex items-center gap-2 min-w-0">
        <span className="font-mono text-[11px] text-white/40 truncate">{job.repo}</span>
        <span className="font-mono text-[11px] text-white/25 shrink-0">{job.job_id.slice(0, 8)}</span>
        <span className="flex-1" />
        {headerRight}
      </div>
      <div className="mt-1 flex items-center gap-2 min-w-0">
        <span className="text-sm text-white/90 truncate">{title}</span>
        {hasIssue && (
          <span
            data-testid="issue-chip"
            className="shrink-0 rounded-md border border-cyan-500/25 bg-cyan-500/10 px-1.5 py-0.5 text-[10px] font-mono text-cyan-300"
          >
            {job.issue_number != null ? `#${job.issue_number}` : "issue"}
          </span>
        )}
      </div>
      {children}
    </div>
  );
}
