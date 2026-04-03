import { Button } from "@/components/ui/Button";
import { Card, CardContent } from "@/components/ui/Card";
import { FilterSelect } from "@/components/ui/FilterSelect";
import { Pagination } from "@/components/ui/Pagination";
import { JobStatusBadge } from "./JobStatusBadge";
import { formatCost, formatDate } from "@/lib/utils";
import { TIER_COLORS } from "@/lib/constants";
import { Link, useNavigate } from "react-router";
import { Play, X, Eye, GitPullRequest, Briefcase } from "lucide-react";
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
  onRun: (jobId: string) => void;
  onCancel: (jobId: string) => void;
  runningJobId?: string;
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
  onRun,
  onCancel,
  runningJobId,
  cancellingJobId,
}: JobsTableProps) {
  const navigate = useNavigate();

  return (
    <Card>
      <CardContent className="p-0">
        {/* Filters */}
        <div className="flex items-center gap-3 border-b border-border px-4 py-3">
          <FilterSelect
            label="Statuses"
            value={filters.status}
            options={statusOptions}
            onChange={onStatusChange}
          />
          <FilterSelect
            label="Repos"
            value={filters.repo}
            options={repoOptions}
            onChange={onRepoChange}
          />
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-white/40">
                <th scope="col" className="text-left px-4 py-3">Job ID</th>
                <th scope="col" className="text-left px-4 py-3">Repo</th>
                <th scope="col" className="text-left px-4 py-3">Task</th>
                <th scope="col" className="text-left px-4 py-3">Status</th>
                <th scope="col" className="text-left px-4 py-3">Tier</th>
                <th scope="col" className="text-right px-4 py-3">Cost</th>
                <th scope="col" className="text-right px-4 py-3">Created</th>
                <th scope="col" className="text-right px-4 py-3">Actions</th>
              </tr>
            </thead>
            <tbody>
              {jobs.map((job) => (
                <tr
                  key={job.job_id}
                  className="border-b border-white/[0.04] hover:bg-cyan-500/[0.02] transition-colors cursor-pointer"
                  onClick={() => navigate(`/jobs/${job.job_id}`)}
                  onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); navigate(`/jobs/${job.job_id}`); } }}
                  tabIndex={0}
                  role="link"
                  aria-label={`View job ${job.job_id}`}
                >
                  <td className="px-4 py-3 font-mono text-xs">{job.job_id.slice(0, 8)}</td>
                  <td className="px-4 py-3 font-mono text-xs">{job.repo}</td>
                  <td className="px-4 py-3 max-w-[200px] truncate">{job.task}</td>
                  <td className="px-4 py-3">
                    <JobStatusBadge status={job.status} />
                  </td>
                  <td className={`px-4 py-3 capitalize ${job.tier ? TIER_COLORS[job.tier] : "text-muted-foreground"}`}>
                    {job.tier ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-right">{formatCost(job.total_cost_usd)}</td>
                  <td className="px-4 py-3 text-right text-muted-foreground">{formatDate(job.created_at)}</td>
                  <td className="px-4 py-3 text-right" onClick={(e) => e.stopPropagation()}>
                    <div className="flex items-center justify-end gap-1">
                      {job.pr_url && (
                        <a href={job.pr_url} target="_blank" rel="noopener noreferrer" title="View Pull Request">
                          <Button variant="ghost" size="icon">
                            <GitPullRequest className="h-3.5 w-3.5 text-green-500" />
                          </Button>
                        </a>
                      )}
                      {(job.status === "running" || job.status === "completed" || job.status === "failed") && (
                        <Link to={`/jobs/${job.job_id}/logs`}>
                          <Button variant="ghost" size="icon" title="View Logs">
                            <Eye className="h-3.5 w-3.5" />
                          </Button>
                        </Link>
                      )}
                      {job.status === "pending" && (
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => onRun(job.job_id)}
                          title="Run"
                          disabled={runningJobId === job.job_id}
                        >
                          <Play className={`h-3.5 w-3.5 ${runningJobId === job.job_id ? "opacity-50" : ""}`} />
                        </Button>
                      )}
                      {(job.status === "pending" || job.status === "running") && (
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => onCancel(job.job_id)}
                          title="Cancel"
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
                  <td colSpan={8} className="px-4 py-2">
                    <div className="flex flex-col items-center justify-center py-16 text-center">
                      <div className="relative">
                        <div className="absolute inset-0 bg-cyan-500/5 blur-2xl rounded-full scale-150" />
                        <Briefcase size={40} className="text-white/10 relative" />
                      </div>
                      <p className="text-white/25 text-sm mt-4 font-medium">No jobs found</p>
                      <p className="text-white/[0.12] text-xs mt-1">Create a new job or adjust your filters</p>
                    </div>
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
