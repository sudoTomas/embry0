import { useState } from "react";
import { useParams, Link } from "react-router";
import { useJob } from "@/hooks/useJobs";
import { useJobLogs } from "@/hooks/useJobLogs";
import { useTraces } from "@/hooks/useTraces";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { JobStatusBadge } from "@/components/jobs/JobStatusBadge";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/Tabs";
import { TracesTable } from "@/components/traces/TracesTable";
import { formatCost, formatDate } from "@/lib/utils";
import { TIER_COLORS } from "@/lib/constants";
import {
  ArrowLeft,
  ScrollText,
  ExternalLink,
  GitPullRequest,
  Clock,
  DollarSign,
  Cpu,
  RefreshCw,
  AlertTriangle,
  CheckCircle,
} from "lucide-react";

function DetailRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <dt className="text-xs text-muted-foreground uppercase tracking-wide">{label}</dt>
      <dd className="text-sm">{children}</dd>
    </div>
  );
}

function JobTracesTab({ jobId }: { jobId: string }) {
  const [filters, setFilters] = useState<{
    offset: number;
  }>({ offset: 0 });
  const limit = 30;
  const { data, isLoading, isError, refetch } = useTraces({ trace_id: jobId, ...filters, limit });

  if (isError) {
    return <PageError message="Failed to load traces" onRetry={() => refetch()} />;
  }
  if (isLoading) {
    return <TableSkeleton columns={9} rows={8} />;
  }
  if (!data) return null;

  return (
    <TracesTable
      traces={data.traces}
      total={data.total}
      offset={filters.offset}
      limit={limit}
      filters={{}}
      onFilterChange={() => {}}
      onPageChange={(offset) => setFilters({ offset })}
    />
  );
}

export function JobDetailPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const { data: job, isLoading, isError, refetch } = useJob(jobId);

  const isRunning = job?.status === "running";
  const logs = useJobLogs(isRunning && jobId ? jobId : "");
  const liveCost = isRunning ? logs.latestCost : (job?.total_cost_usd ?? 0);

  if (isError) {
    return <PageError message="Failed to load job details" onRetry={() => refetch()} />;
  }

  if (isLoading || !job) {
    return <div className="text-muted-foreground">Loading...</div>;
  }

  const detailsContent = (
    <div className="space-y-6">
      {/* Task */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Task</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm whitespace-pre-wrap">{job.task}</p>
        </CardContent>
      </Card>

      {/* Overview Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Job Info */}
        <Card>
          <CardHeader>
            <CardTitle className="text-lg flex items-center gap-2">
              <Cpu className="h-4 w-4" />
              Job Info
            </CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-2 gap-4">
              <DetailRow label="Repository">
                <span className="font-mono text-xs">{job.repo}</span>
              </DetailRow>
              <DetailRow label="Issue">
                {job.issue_number != null ? (
                  <span className="font-mono">#{job.issue_number}</span>
                ) : (
                  <span className="text-muted-foreground">--</span>
                )}
              </DetailRow>
              <DetailRow label="Tier">
                <span className={`capitalize ${job.tier ? TIER_COLORS[job.tier] : "text-muted-foreground"}`}>
                  {job.tier ?? "--"}
                </span>
              </DetailRow>
              <DetailRow label="Provider Mode">
                <span className="font-mono text-xs">{job.provider_mode ?? "--"}</span>
              </DetailRow>
              <DetailRow label="Model">
                <span className="font-mono text-xs">{job.model ?? "--"}</span>
              </DetailRow>
              <DetailRow label="Attempts">
                <span className="flex items-center gap-1">
                  <RefreshCw className="h-3 w-3" />
                  {job.attempts}
                </span>
              </DetailRow>
            </dl>
          </CardContent>
        </Card>

        {/* Timing & Cost */}
        <Card>
          <CardHeader>
            <CardTitle className="text-lg flex items-center gap-2">
              <Clock className="h-4 w-4" />
              Timing & Cost
            </CardTitle>
          </CardHeader>
          <CardContent>
            <dl className="grid grid-cols-2 gap-4">
              <DetailRow label="Created">
                {formatDate(job.created_at)}
              </DetailRow>
              <DetailRow label="Started">
                {job.started_at ? formatDate(job.started_at) : <span className="text-muted-foreground">--</span>}
              </DetailRow>
              <DetailRow label="Finished">
                {job.finished_at ? formatDate(job.finished_at) : <span className="text-muted-foreground">--</span>}
              </DetailRow>
              <DetailRow label="Total Cost">
                <span className="flex items-center gap-1">
                  <DollarSign className="h-3 w-3" />
                  {formatCost(liveCost)}
                  {isRunning && logs.isConnected && (
                    <span className="ml-1 text-xs text-green-400 animate-pulse">Live</span>
                  )}
                </span>
              </DetailRow>
            </dl>
          </CardContent>
        </Card>
      </div>

      {/* Pull Request */}
      {job.pr_url && (
        <Card className="border-green-500/30 bg-green-500/5">
          <CardHeader>
            <CardTitle className="text-lg flex items-center gap-2 text-green-600 dark:text-green-400">
              <GitPullRequest className="h-4 w-4" />
              Pull Request
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <a
              href={job.pr_url}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-2 text-sm text-muted-foreground hover:underline font-mono break-all"
            >
              {job.pr_url}
            </a>
            <a href={job.pr_url} target="_blank" rel="noopener noreferrer">
              <Button className="bg-green-600 text-white hover:bg-green-700">
                <GitPullRequest className="h-4 w-4 mr-2" />
                View Pull Request
                <ExternalLink className="h-3.5 w-3.5 ml-1" />
              </Button>
            </a>
          </CardContent>
        </Card>
      )}

      {/* Validation Summary */}
      {job.validation_summary && (
        <Card>
          <CardHeader>
            <CardTitle className="text-lg flex items-center gap-2">
              <CheckCircle className="h-4 w-4" />
              Validation Summary
            </CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm whitespace-pre-wrap">{job.validation_summary}</p>
          </CardContent>
        </Card>
      )}

      {/* Error Message */}
      {job.error_message && (
        <Card className="border-destructive/50">
          <CardHeader>
            <CardTitle className="text-lg flex items-center gap-2 text-destructive">
              <AlertTriangle className="h-4 w-4" />
              Error
            </CardTitle>
          </CardHeader>
          <CardContent>
            <pre className="text-sm text-destructive whitespace-pre-wrap font-mono bg-destructive/10 rounded-md p-3">
              {job.error_message}
            </pre>
          </CardContent>
        </Card>
      )}
    </div>
  );

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
            <h1 className="text-2xl font-bold">Job Detail</h1>
            <JobStatusBadge status={job.status} />
          </div>
          <p className="text-sm text-muted-foreground font-mono">{job.job_id}</p>
        </div>
        <Link to={`/jobs/${job.job_id}/logs`}>
          <Button variant="outline" size="sm">
            <ScrollText className="h-4 w-4 mr-2" />
            View Logs
          </Button>
        </Link>
      </div>

      <Tabs defaultValue="details">
        <TabsList>
          <TabsTrigger value="details">Details</TabsTrigger>
          <TabsTrigger value="traces">Traces</TabsTrigger>
        </TabsList>
        <TabsContent value="details">
          {detailsContent}
        </TabsContent>
        <TabsContent value="traces">
          <JobTracesTab jobId={job.job_id} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
