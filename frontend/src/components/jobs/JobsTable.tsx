import { Button, buttonVariants } from "@/components/ui/Button";
import { Card, CardContent } from "@/components/ui/Card";
import { FilterSelect } from "@/components/ui/FilterSelect";
import { Pagination } from "@/components/ui/Pagination";
import { JobStatusBadge } from "./JobStatusBadge";
import { Badge } from "@/components/ui/Badge";
import { EmptyJobsState } from "./EmptyJobsState";
import { formatCost, formatDateTime } from "@/lib/utils";
import { statusLabel } from "@/lib/status";
import { jobTitle, shortJobId } from "@/lib/jobTitle";
import { isCancellable } from "@/lib/jobStatus";
import { Link, useNavigate } from "react-router";
import { X, Eye, GitPullRequest } from "lucide-react";
import type { JobResponse } from "@/lib/types";

interface JobsTableProps {
  jobs: JobResponse[];
  total: number;
  offset: number;
  limit: number;
  filters: { status?: string; repo?: string };
  repoOptions: string[];
  statusOptions: string[];
  onStatusChange: (v: string | undefined) => void;
  onRepoChange: (v: string | undefined) => void;
  onPageChange: (offset: number) => void;
  onCancel: (jobId: string) => void;
  cancellingJobId?: string;
}

export function JobsTable({
  jobs,
  total,
  offset,
  limit,
  filters,
  repoOptions,
  statusOptions,
  onStatusChange,
  onRepoChange,
  onPageChange,
  onCancel,
  cancellingJobId,
}: JobsTableProps) {
  const navigate = useNavigate();

  return (
    <Card>
      <CardContent className="p-0">
        {/* Filters */}
        <div className="flex flex-wrap items-center gap-3 border-b border-border px-3 py-2.5">
          <FilterSelect
            label="Statuses"
            value={filters.status}
            options={statusOptions}
            optionLabel={statusLabel}
            onChange={onStatusChange}
          />
          <FilterSelect
            label="Repos"
            value={filters.repo}
            options={repoOptions}
            onChange={onRepoChange}
          />
        </div>

        {/* Below sm the job id, repo, cost and created columns step aside and
            the task takes the room that is left, with the short job id under
            it, so the status pill and the actions stay on a phone screen. */}
        <div className="overflow-x-auto">
          <table className="data-table">
            <thead>
              <tr>
                <th scope="col" className="hidden sm:table-cell">Job ID</th>
                <th scope="col" className="hidden sm:table-cell">Repo</th>
                <th scope="col">Task</th>
                <th scope="col">Status</th>
                <th scope="col" className="hidden text-right sm:table-cell">Cost</th>
                <th scope="col" className="hidden text-right sm:table-cell">Created</th>
                <th scope="col" className="text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <tr
                  key={job.job_id}
                  className="cursor-pointer"
                  onClick={() => navigate(`/jobs/${job.job_id}`)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); navigate(`/jobs/${job.job_id}`); } }}
                  tabIndex={0}
                  role="link"
                  aria-label={`View job ${job.job_id}`}
                >
                  <td className="hidden whitespace-nowrap font-mono text-xs sm:table-cell">
                    <span title={job.job_id}>{shortJobId(job.job_id)}</span>
                  </td>
                  <td className="hidden whitespace-nowrap font-mono text-xs sm:table-cell">{job.repo}</td>
                  <td className="w-full max-w-0 sm:w-auto sm:max-w-[200px]" title={job.task}>
                    {/* Two lines on a phone, where one cut every design audit to
                        "Design audit of ra..." and told none apart; one from sm up. */}
                    <div className="line-clamp-2 break-words sm:line-clamp-1">
                      {job.area && (
                        <Badge tone="purple" className="mr-1.5" title="The part of the repository its pull request changes">
                          {job.area}
                        </Badge>
                      )}
                      {jobTitle(job.task)}
                    </div>
                    <div className="font-mono text-xs text-fg-muted sm:hidden" title={job.job_id}>
                      {shortJobId(job.job_id)}
                    </div>
                  </td>
                  <td>
                    <JobStatusBadge status={job.status} />
                  </td>
                  <td className="hidden whitespace-nowrap text-right sm:table-cell">{formatCost(job.total_cost_usd)}</td>
                  <td className="hidden whitespace-nowrap text-right text-muted-foreground sm:table-cell">{formatDateTime(job.created_at)}</td>
                  <td className="text-right" onClick={(e) => e.stopPropagation()}>
                    <div className="flex items-center justify-end gap-1">
                      {/* Each action is one named control: a link styled as a
                          button, never a button nested in a link. */}
                      {job.pr_url && (
                        <a
                          href={job.pr_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          title="View Pull Request"
                          aria-label="View pull request"
                          className={buttonVariants({ variant: "ghost", size: "icon-sm" })}
                        >
                          <GitPullRequest className="h-3.5 w-3.5 text-success" />
                        </a>
                      )}
                      {job.status !== "pending" && (
                        <Link
                          to={`/jobs/${job.job_id}`}
                          title="Open job"
                          aria-label="Open job"
                          className={buttonVariants({ variant: "ghost", size: "icon-sm" })}
                        >
                          <Eye className="h-3.5 w-3.5" />
                        </Link>
                      )}
                      {isCancellable(job.status) && (
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          onClick={() => onCancel(job.job_id)}
                          title="Cancel"
                          aria-label="Cancel job"
                          disabled={cancellingJobId === job.job_id}
                        >
                          <X className={`h-3.5 w-3.5 ${cancellingJobId === job.job_id ? "opacity-50" : ""}`} />
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
              {jobs.length === 0 && (
                <tr>
                  <td colSpan={7}>
                    <EmptyJobsState />
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
