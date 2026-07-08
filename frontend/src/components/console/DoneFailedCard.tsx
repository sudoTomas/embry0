import { memo } from "react";
import { GitPullRequest } from "lucide-react";
import { Badge } from "@/components/ui/Badge";
import { JobStatusBadge } from "@/components/jobs/JobStatusBadge";
import { formatCost, formatDate } from "@/lib/utils";
import type { JobResponse } from "@/lib/types";
import { ConsoleCardShell } from "./ConsoleCardShell";

/**
 * Board card for terminal jobs (Done and Failed lanes). The status sub-badge
 * distinguishes completed/pr_merged and the five failure-lane statuses; the
 * deliverable is a PR pill or an explicit "no PR" (never silence); failures
 * additionally surface error_code as a chip — the DB has carried these codes
 * with no UI surface until this board.
 */
export const DoneFailedCard = memo(function DoneFailedCard({ job }: { job: JobResponse }) {
  return (
    <ConsoleCardShell job={job} headerRight={<JobStatusBadge status={job.status} />}>
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {job.pr_url ? (
          <a
            href={job.pr_url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={(e) => e.stopPropagation()}
            data-testid="pr-pill"
            className="inline-flex items-center gap-1 rounded-md border border-success/25 bg-success/10 px-1.5 py-0.5 text-[10px] font-semibold text-success transition-colors hover:bg-success/20"
          >
            <GitPullRequest className="w-3 h-3" />
            PR
          </a>
        ) : (
          <span data-testid="no-pr" className="text-[10px] text-white/30">
            no PR
          </span>
        )}
        {job.error_code && (
          <Badge tone="error" className="font-mono">
            {job.error_code}
          </Badge>
        )}
      </div>
      <div className="mt-2 flex items-center gap-3 text-[11px] text-white/40">
        <span>{formatCost(job.total_cost_usd)}</span>
        {job.finished_at && <span>finished {formatDate(job.finished_at)}</span>}
      </div>
    </ConsoleCardShell>
  );
});
