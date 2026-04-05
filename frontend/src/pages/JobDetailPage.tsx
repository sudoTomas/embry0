import { Link, useParams } from "react-router";
import { ArrowLeft, ExternalLink, Clock, DollarSign } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { useJob } from "@/hooks/useJobs";
import { useJobInputs } from "@/hooks/useInputs";
import { JobStatusBadge } from "@/components/jobs/JobStatusBadge";
import { ResultsSummary } from "@/components/jobs/ResultsSummary";
import { ErrorSummary } from "@/components/jobs/ErrorSummary";
import { AwaitingInputCard } from "@/components/jobs/AwaitingInputCard";
import { useJobEvents } from "@/hooks/useJobEvents";
import { JobEventTimeline } from "@/components/jobs/JobEventTimeline";
import { formatDate } from "@/lib/utils";

export function JobDetailPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const { data: job, isLoading, isError } = useJob(jobId);
  const { data: jobInputs } = useJobInputs(jobId ?? "");

  // Connect to pipeline events for any non-pending job
  const shouldConnect = !!job && job.status !== "pending";
  const { events: pipelineEvents, connected: pipelineConnected } = useJobEvents(
    jobId,
    shouldConnect,
  );

  if (isError) return <div className="p-8 text-center text-red-400">Failed to load job</div>;
  if (isLoading || !job) return <div className="p-8 text-center text-white/40">Loading...</div>;

  const isActive = job.status === "running" || job.status === "awaiting_input";

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Link to="/jobs">
          <Button variant="ghost" size="icon">
            <ArrowLeft className="h-4 w-4" />
          </Button>
        </Link>
        <div className="flex-1">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold">{job.repo}</h1>
            <JobStatusBadge status={job.status} />
          </div>
          <p className="text-sm text-muted-foreground font-mono">{job.job_id}</p>
        </div>
        {job.issue_number && (
          <a
            href={`https://github.com/${job.repo}/issues/${job.issue_number}`}
            target="_blank"
            rel="noopener noreferrer"
          >
            <Button variant="outline" size="sm" className="gap-1.5">
              Issue #{job.issue_number} <ExternalLink className="w-3 h-3" />
            </Button>
          </a>
        )}
      </div>

      {/* Task description */}
      <div className="px-4 py-3 rounded-lg bg-white/[0.03] border border-white/[0.06] text-sm text-white/70 whitespace-pre-wrap">
        {job.task}
      </div>

      {/* Job metadata bar */}
      <div className="flex items-center gap-6 text-sm text-white/50">
        {job.started_at && (
          <div className="flex items-center gap-1.5">
            <Clock className="w-3.5 h-3.5" />
            <span>Started {formatDate(job.started_at)}</span>
          </div>
        )}
        {job.total_cost_usd > 0 && (
          <div className="flex items-center gap-1.5">
            <DollarSign className="w-3.5 h-3.5" />
            <span>${job.total_cost_usd.toFixed(4)}</span>
          </div>
        )}
        {job.finished_at && (
          <span>Finished {formatDate(job.finished_at)}</span>
        )}
      </div>

      {/* PR link */}
      {job.pr_url && (
        <div className="flex items-center gap-2 text-sm px-4 py-2 rounded-lg bg-purple-500/10 border border-purple-500/20">
          <span className="text-purple-300">PR:</span>
          <a href={job.pr_url} target="_blank" rel="noopener noreferrer" className="text-purple-400 hover:underline">
            {job.pr_url}
          </a>
        </div>
      )}

      {/* Error message */}
      {job.status === "failed" && job.error_message && (
        <ErrorSummary
          errorMessage={job.error_message}
          totalCost={job.total_cost_usd}
          startedAt={job.started_at}
          finishedAt={job.finished_at}
          nodeStates={{}}
        />
      )}

      {/* Completed summary */}
      {job.status === "completed" && (
        <ResultsSummary
          prUrl={job.pr_url}
          totalCost={job.total_cost_usd}
          startedAt={job.started_at}
          finishedAt={job.finished_at}
          agentsRun={0}
        />
      )}

      {/* Awaiting input */}
      {job.status === "awaiting_input" && (
        <AwaitingInputCard
          pendingInputs={{}}
          jobInputs={jobInputs ?? []}
          jobId={job.job_id}
        />
      )}

      {/* Pipeline Events Timeline — shown for all non-pending jobs */}
      {job.status !== "pending" && (
        <div className="px-4 py-3 rounded-lg bg-white/[0.03] border border-white/[0.06]">
          <h3 className="text-sm font-medium text-white/60 mb-3 flex items-center gap-2">
            Pipeline Events
            {isActive && pipelineConnected && (
              <span className="h-2 w-2 rounded-full bg-green-400 animate-pulse" />
            )}
          </h3>
          <JobEventTimeline events={pipelineEvents} connected={pipelineConnected} />
        </div>
      )}

      {/* Pending state */}
      {job.status === "pending" && (
        <div className="text-center py-12 text-white/30 text-sm">
          Waiting to start...
        </div>
      )}
    </div>
  );
}
