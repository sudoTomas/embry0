import { useEffect, useState, type JSX } from "react";
import { useNavigate, useParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Send, RefreshCw, X, ExternalLink } from "lucide-react";
import { toast } from "sonner";
import { OperationGlyph } from "@/components/divine/OperationGlyph";
import { OPERATION_NUMERAL, OPERATION_ELEMENT } from "@/components/divine/operations";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Select } from "@/components/ui/Select";
import { TableSkeleton } from "@/components/TableSkeleton";
import { PageError } from "@/components/PageError";
import { isNotFound } from "@/api/errorDetail";
import { IssueStatusBadge } from "@/components/issues/IssueStatusBadge";
import { IssuePriorityBadge } from "@/components/issues/IssuePriorityBadge";
import { AgentIndicator } from "@/components/issues/AgentIndicator";
import { IssueActivityFeed } from "@/components/issues/IssueActivityFeed";
import { IssueQuestionsPanel } from "@/components/issues/IssueQuestionsPanel";
import { IssueBody } from "@/components/issues/IssueBody";
import { LabelInput } from "@/components/issues/LabelInput";
import { updateIssueNotificationChannels } from "@/api/issues";
import { useIssue, useIssueActivity, useTriageIssue, useSyncIssue, useDeleteIssue, useUpdateIssue } from "@/hooks/useIssues";
import { formatDate } from "@/lib/utils";
import { formatCost } from "@/lib/utils";
import { providerLabel, statusLabel } from "@/lib/status";
import type { NotificationChannel } from "@/lib/types";
import { toastApiError } from "@/api/errorToast";
import { trackerName } from "@/lib/forgeLinks";

const NOTIFICATION_CHANNELS = ["dashboard", "telegram", "github", "linear", "gitlab", "slack"] as NotificationChannel[];

export function IssueDetailPage(): JSX.Element {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const { data: issue, isLoading, isError, error, refetch } = useIssue(id);
  const { data: activity = [] } = useIssueActivity(id);
  const triageIssue = useTriageIssue();
  const syncIssue = useSyncIssue();
  const deleteIssue = useDeleteIssue();
  const updateIssue = useUpdateIssue();
  const queryClient = useQueryClient();

  // Local optimistic state for the notification-channel checkboxes — kept in
  // sync with the server value via the effect below so polling refetches
  // (useIssue refetches every 10s) don't clobber an in-flight toggle.
  const [channels, setChannels] = useState<NotificationChannel[]>(
    issue?.notification_channels ?? ["dashboard"],
  );
  useEffect(() => {
    if (issue?.notification_channels) {
      setChannels(issue.notification_channels);
    }
  }, [issue?.notification_channels]);

  const handleFieldUpdate = (field: string, value: unknown): void => {
    updateIssue.mutate({ id: issue!.id, [field]: value }, { onError: (e) => toastApiError(e, "Failed to update") });
  };

  const toggleChannel = (ch: NotificationChannel): void => {
    if (!issue) return;
    const next = channels.includes(ch)
      ? channels.filter((c) => c !== ch)
      : [...channels, ch];
    setChannels(next);
    updateIssueNotificationChannels(issue.id, next)
      .then(() => {
        // Invalidate both the detail query and the list so badges/filters
        // reflect the change without waiting for the 10s poll.
        queryClient.invalidateQueries({ queryKey: ["issues", issue.id] });
        queryClient.invalidateQueries({ queryKey: ["issues"] });
      })
      .catch((e: unknown) => {
        // Roll back the optimistic state on failure.
        setChannels(channels);
        toastApiError(e, "Failed to update notification channels");
      });
  };

  if (isNotFound(error)) return <PageError message={`No issue ${id}`} back={{ to: "/issues", label: "All issues" }} />;
  if (isError) return <PageError message="Failed to load issue" onRetry={() => refetch()} />;
  if (isLoading || !issue) return <TableSkeleton columns={4} rows={8} />;

  const canTriage = issue.status !== "triaging" && issue.status !== "closed" && issue.status !== "cancelled";
  const canSync = issue.github_sync_enabled;
  const canCancel = issue.status !== "closed" && issue.status !== "cancelled";

  const handleTriage = () => {
    triageIssue.mutate(id!, {
      // The API answers 200 even when no job could be started; the body says so.
      onSuccess: (result) =>
        result.dispatch_error
          ? toast.error(`Triage did not start: ${result.dispatch_error}`)
          : toast.success("Triage started"),
      onError: (e) => toastApiError(e, "Failed to triage"),
    });
  };

  const handleSync = () => {
    syncIssue.mutate(id!, {
      onSuccess: () => toast.success("Synced with GitHub"),
      onError: (e) => toastApiError(e, "Failed to sync"),
    });
  };

  // DELETE /issues/{id} soft-cancels: the issue, its children and their active
  // jobs become cancelled and the row stays (cancelled -> open is allowed).
  const handleCancel = () => {
    if (!confirm("Cancel this issue? Its running jobs are cancelled too.")) return;
    deleteIssue.mutate(id!, {
      onSuccess: () => toast.success("Issue cancelled"),
      onError: (e) => toastApiError(e, "Failed to cancel"),
    });
  };

  return (
    <div className="space-y-6">
      {/* Header. It wraps: on a phone the actions take a line of their own
          under the title instead of running off the right edge. */}
      <div className="flex flex-wrap items-start gap-4">
        <Button variant="ghost" size="icon" onClick={() => navigate("/issues")} aria-label="Back to issues">
          <ArrowLeft className="h-4 w-4" />
        </Button>

        <span className="flex items-center justify-center w-9 h-9 shrink-0 mt-0.5">
          <OperationGlyph operation="calcinate" size={36} titled />
        </span>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="text-xl font-bold truncate leading-tight">{issue.title}</h1>
            {issue.active_agent && <AgentIndicator agentType={issue.active_agent} size="md" />}
          </div>
          <div className="flex items-center gap-2 mt-0.5">
            <span className="text-[10px] uppercase tracking-[0.18em] text-primary/65 font-mono">
              Op {OPERATION_NUMERAL.calcinate} · calcinate · {OPERATION_ELEMENT.calcinate}
            </span>
            <span className="text-[10px] text-muted-foreground/40">·</span>
            <p className="text-xs text-muted-foreground font-mono">{issue.id}</p>
          </div>
        </div>

        {/* Action buttons */}
        <div className="flex flex-wrap items-center gap-2 shrink-0">
          <Button
            size="sm"
            variant="outline"
            disabled={!canTriage || triageIssue.isPending}
            onClick={handleTriage}
            className="gap-1.5"
          >
            <Send className="h-3.5 w-3.5" />
            {triageIssue.isPending ? "Triaging..." : "Send to triage"}
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
              onClick={handleCancel}
              className="gap-1.5 text-destructive hover:text-destructive border-destructive/30 hover:border-destructive/50"
            >
              <X className="h-3.5 w-3.5" />
              Cancel
            </Button>
          )}
        </div>
      </div>

      {/* Questions panel – shown when pipeline is waiting for user input */}
      {issue.status === "awaiting_input" && (
        <IssueQuestionsPanel issueId={issue.id} />
      )}

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
                <IssueBody body={issue.body} />
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
                  Child issues
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
                        <span className="text-xs text-white/60">{statusLabel(status)}</span>
                        {cost !== null && (
                          <span className="text-xs text-muted-foreground">{formatCost(cost)}</span>
                        )}
                        {prUrl && (
                          <a
                            href={prUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            onClick={(e) => e.stopPropagation()}
                            className="ml-auto flex items-center gap-1 text-xs text-fg hover:text-primary hover:underline"
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
                <Select
                  aria-label="Status"
                  value={issue.status}
                  onChange={(e) => handleFieldUpdate("status", e.target.value)}
                  className="h-8 text-xs"
                >
                  <option value="open">Open</option>
                  <option value="triaging">Triaging</option>
                  <option value="awaiting_input">Awaiting input</option>
                  <option value="in_progress">In progress</option>
                  <option value="closed">Closed</option>
                  <option value="cancelled">Cancelled</option>
                </Select>
              </div>

              {/* Priority */}
              <div>
                <p className="text-xs text-muted-foreground mb-1.5">Priority</p>
                <Select
                  aria-label="Priority"
                  value={issue.priority}
                  onChange={(e) => handleFieldUpdate("priority", e.target.value)}
                  className="h-8 text-xs"
                >
                  <option value="critical">Critical</option>
                  <option value="high">High</option>
                  <option value="medium">Medium</option>
                  <option value="low">Low</option>
                </Select>
              </div>

              {/* Labels */}
              <div>
                <p className="text-xs text-muted-foreground mb-1.5">Labels</p>
                <LabelInput
                  value={issue.labels}
                  onChange={(labels) => handleFieldUpdate("labels", labels)}
                />
              </div>

              {/* Repo */}
              {issue.repo && (
                <div>
                  <p className="text-xs text-muted-foreground mb-1.5">Repository</p>
                  <p className="text-sm font-mono text-white/70">{issue.repo}</p>
                </div>
              )}

              {/* Tracker link (GitHub / GitLab / Linear) */}
              {(issue.github_sync_enabled || issue.tracker) && (
                <div>
                  <p className="text-xs text-muted-foreground mb-1.5">{providerLabel(issue.tracker ?? "github")}</p>
                  <div className="space-y-1">
                    {issue.tracker_key && (
                      <div className="flex items-center gap-1.5">
                        <span className="text-sm text-white/70">
                          {issue.tracker === "linear" ? issue.tracker_key : `#${issue.tracker_key}`}
                        </span>
                        {issue.tracker_url && (
                          <a
                            aria-label={`Open ${issue.tracker === "linear" ? issue.tracker_key : `#${issue.tracker_key}`} in ${trackerName(issue.tracker)}`}
                            href={issue.tracker_url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-fg hover:text-primary hover:underline"
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

              {/* Notification channels — fan-out targets for agent ask-user
                  questions. Toggling fires updateIssueNotificationChannels
                  immediately; the surrounding query is invalidated on success. */}
              <div>
                <p className="text-xs text-muted-foreground mb-1.5">Notify via</p>
                <div className="flex items-center gap-3 text-sm flex-wrap">
                  {NOTIFICATION_CHANNELS.map((ch) => (
                    <label key={ch} className="flex items-center gap-1.5 cursor-pointer">
                      <input
                        type="checkbox"
                        checked={channels.includes(ch)}
                        onChange={() => toggleChannel(ch)}
                        className="rounded border-white/20"
                      />
                      <span>{providerLabel(ch)}</span>
                    </label>
                  ))}
                </div>
                {(["github", "linear", "gitlab"] as const)
                  .filter((tracker) => channels.includes(tracker) && issue.tracker !== tracker)
                  .map((tracker) => (
                    <p key={tracker} className="text-xs text-amber-400 mt-1.5">
                      No {providerLabel(tracker)} issue linked — comment dispatch will be a
                      no-op.
                    </p>
                  ))}
              </div>

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
