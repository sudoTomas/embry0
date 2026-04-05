import { useNavigate, useParams } from "react-router";
import { ArrowLeft, Send, RefreshCw, X, ExternalLink } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { TableSkeleton } from "@/components/TableSkeleton";
import { PageError } from "@/components/PageError";
import { IssueStatusBadge } from "@/components/issues/IssueStatusBadge";
import { IssuePriorityBadge } from "@/components/issues/IssuePriorityBadge";
import { AgentIndicator } from "@/components/issues/AgentIndicator";
import { IssueActivityFeed } from "@/components/issues/IssueActivityFeed";
import { useIssue, useIssueActivity, useTriageIssue, useSyncIssue, useDeleteIssue } from "@/hooks/useIssues";
import { formatDate } from "@/lib/utils";
import { formatCost } from "@/lib/utils";

export function IssueDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const { data: issue, isLoading, isError, refetch } = useIssue(id);
  const { data: activity = [] } = useIssueActivity(id);
  const triageIssue = useTriageIssue();
  const syncIssue = useSyncIssue();
  const deleteIssue = useDeleteIssue();

  if (isError) return <PageError message="Failed to load issue" onRetry={() => refetch()} />;
  if (isLoading || !issue) return <TableSkeleton columns={4} rows={8} />;

  const canTriage = issue.status !== "triaging" && issue.status !== "closed" && issue.status !== "cancelled";
  const canSync = issue.github_sync_enabled;
  const canCancel = issue.status !== "closed" && issue.status !== "cancelled";

  const handleTriage = () => {
    triageIssue.mutate(id!, {
      onSuccess: () => toast.success("Triage started"),
      onError: (e) => toast.error(e instanceof Error ? e.message : "Failed to triage"),
    });
  };

  const handleSync = () => {
    syncIssue.mutate(id!, {
      onSuccess: () => toast.success("Synced with GitHub"),
      onError: (e) => toast.error(e instanceof Error ? e.message : "Failed to sync"),
    });
  };

  const handleDelete = () => {
    if (!confirm("Delete this issue? This cannot be undone.")) return;
    deleteIssue.mutate(id!, {
      onSuccess: () => {
        toast.success("Issue deleted");
        navigate("/issues");
      },
      onError: (e) => toast.error(e instanceof Error ? e.message : "Failed to delete"),
    });
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start gap-4">
        <Button variant="ghost" size="icon" onClick={() => navigate("/issues")} aria-label="Back to issues">
          <ArrowLeft className="h-4 w-4" />
        </Button>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-2xl font-bold truncate">{issue.title}</h1>
            {issue.active_agent && <AgentIndicator agentType={issue.active_agent} size="md" />}
          </div>
          <p className="text-xs text-muted-foreground font-mono mt-0.5">{issue.id}</p>
        </div>

        {/* Action buttons */}
        <div className="flex items-center gap-2 shrink-0">
          <Button
            size="sm"
            variant="outline"
            disabled={!canTriage || triageIssue.isPending}
            onClick={handleTriage}
            className="gap-1.5"
          >
            <Send className="h-3.5 w-3.5" />
            {triageIssue.isPending ? "Triaging..." : "Send to Triage"}
          </Button>

          {canSync && (
            <Button
              size="sm"
              variant="outline"
              disabled={syncIssue.isPending}
              onClick={handleSync}
              className="gap-1.5"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${syncIssue.isPending ? "animate-spin" : ""}`} />
              Sync
            </Button>
          )}

          {canCancel && (
            <Button
              size="sm"
              variant="outline"
              disabled={deleteIssue.isPending}
              onClick={handleDelete}
              className="gap-1.5 text-destructive hover:text-destructive border-destructive/30 hover:border-destructive/50"
            >
              <X className="h-3.5 w-3.5" />
              Cancel
            </Button>
          )}
        </div>
      </div>

      {/* Two-column layout */}
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_300px] gap-6">
        {/* Left column */}
        <div className="space-y-6">
          {/* Body */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Description</CardTitle>
            </CardHeader>
            <CardContent>
              {issue.body ? (
                <pre className="text-sm text-white/80 whitespace-pre-wrap font-sans leading-relaxed">
                  {issue.body}
                </pre>
              ) : (
                <p className="text-sm text-muted-foreground">No description provided.</p>
              )}
            </CardContent>
          </Card>

          {/* Child issues */}
          {issue.children && issue.children.length > 0 && (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base">
                  Child Issues
                  <span className="ml-2 text-sm font-normal text-muted-foreground">
                    ({issue.children.length})
                  </span>
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <div className="divide-y divide-white/[0.04]">
                  {issue.children.map((child) => (
                    <div
                      key={child.id}
                      className="flex items-center gap-3 px-6 py-3 hover:bg-white/[0.02] cursor-pointer transition-colors"
                      onClick={() => navigate(`/issues/${child.id}`)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          navigate(`/issues/${child.id}`);
                        }
                      }}
                      tabIndex={0}
                      role="link"
                      aria-label={`View child issue: ${child.title}`}
                    >
                      <IssueStatusBadge status={child.status} />
                      <span className="flex-1 text-sm truncate">{child.title}</span>
                      <IssuePriorityBadge priority={child.priority} />
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          )}

          {/* Jobs */}
          {issue.jobs && issue.jobs.length > 0 && (
            <Card>
              <CardHeader className="pb-3">
                <CardTitle className="text-base">
                  Jobs
                  <span className="ml-2 text-sm font-normal text-muted-foreground">
                    ({issue.jobs.length})
                  </span>
                </CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <div className="divide-y divide-white/[0.04]">
                  {issue.jobs.map((job) => {
                    const jobId = String(job.job_id ?? "");
                    const status = String(job.status ?? "");
                    const prUrl = job.pr_url ? String(job.pr_url) : null;
                    const cost = typeof job.total_cost_usd === "number" ? job.total_cost_usd : null;

                    return (
                      <div
                        key={jobId}
                        className="flex items-center gap-3 px-6 py-3 hover:bg-white/[0.02] cursor-pointer transition-colors"
                        onClick={() => navigate(`/jobs/${jobId}`)}
                        onKeyDown={(e) => {
                          if (e.key === "Enter" || e.key === " ") {
                            e.preventDefault();
                            navigate(`/jobs/${jobId}`);
                          }
                        }}
                        tabIndex={0}
                        role="link"
                        aria-label={`View job: ${jobId}`}
                      >
                        <span className="text-xs font-mono text-muted-foreground shrink-0">{jobId}</span>
                        <span className="text-xs text-white/60 capitalize">{status.replace("_", " ")}</span>
                        {cost !== null && (
                          <span className="text-xs text-muted-foreground">{formatCost(cost)}</span>
                        )}
                        {prUrl && (
                          <a
                            href={prUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={(e) => e.stopPropagation()}
                            className="ml-auto flex items-center gap-1 text-xs text-primary hover:underline"
                          >
                            PR <ExternalLink className="h-3 w-3" />
                          </a>
                        )}
                      </div>
                    );
                  })}
                </div>
              </CardContent>
            </Card>
          )}

          {/* Activity */}
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Activity</CardTitle>
            </CardHeader>
            <CardContent>
              <IssueActivityFeed entries={activity} />
            </CardContent>
          </Card>
        </div>

        {/* Right sidebar */}
        <div className="space-y-4">
          <Card>
            <CardContent className="pt-6 space-y-4">
              {/* Status */}
              <div>
                <p className="text-xs text-muted-foreground mb-1.5">Status</p>
                <IssueStatusBadge status={issue.status} />
              </div>

              {/* Priority */}
              <div>
                <p className="text-xs text-muted-foreground mb-1.5">Priority</p>
                <IssuePriorityBadge priority={issue.priority} />
              </div>

              {/* Labels */}
              {issue.labels.length > 0 && (
                <div>
                  <p className="text-xs text-muted-foreground mb-1.5">Labels</p>
                  <div className="flex flex-wrap gap-1">
                    {issue.labels.map((label) => (
                      <span
                        key={label}
                        className="inline-flex items-center rounded-md bg-zinc-700/50 px-2 py-0.5 text-xs text-zinc-300"
                      >
                        {label}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Repo */}
              {issue.repo && (
                <div>
                  <p className="text-xs text-muted-foreground mb-1.5">Repository</p>
                  <p className="text-sm font-mono text-white/70">{issue.repo}</p>
                </div>
              )}

              {/* GitHub sync info */}
              {issue.github_sync_enabled && (
                <div>
                  <p className="text-xs text-muted-foreground mb-1.5">GitHub</p>
                  <div className="space-y-1">
                    {issue.github_number && (
                      <div className="flex items-center gap-1.5">
                        <span className="text-sm text-white/70">#{issue.github_number}</span>
                        {issue.github_url && (
                          <a
                            href={issue.github_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-primary hover:underline"
                          >
                            <ExternalLink className="h-3.5 w-3.5" />
                          </a>
                        )}
                      </div>
                    )}
                    {issue.github_synced_at && (
                      <p className="text-xs text-muted-foreground">
                        Synced {formatDate(issue.github_synced_at)}
                      </p>
                    )}
                  </div>
                </div>
              )}

              {/* Dates */}
              <div className="border-t border-white/[0.06] pt-3 space-y-2">
                <div>
                  <p className="text-xs text-muted-foreground">Created</p>
                  <p className="text-xs text-white/60">{formatDate(issue.created_at)}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Updated</p>
                  <p className="text-xs text-white/60">{formatDate(issue.updated_at)}</p>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
