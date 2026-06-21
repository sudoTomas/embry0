import { useQuery } from "@tanstack/react-query";
import { Activity, GitBranch } from "lucide-react";

import {
  fetchEvents,
  fetchGitActivity,
  type AgentEvent,
  type AgentGitActivity,
} from "@/api/agent";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { Heartbeat } from "@/components/vitals";

const REFETCH_MS = 5_000;

function formatRelative(iso: string): string {
  const ts = new Date(iso).getTime();
  if (Number.isNaN(ts)) return "";
  const diffSec = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (diffSec < 60) return `${diffSec}s ago`;
  const diffMin = Math.round(diffSec / 60);
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffHr = Math.round(diffMin / 60);
  if (diffHr < 48) return `${diffHr}h ago`;
  const diffDay = Math.round(diffHr / 24);
  return `${diffDay}d ago`;
}

function EventRow({ event }: { event: AgentEvent }) {
  return (
    <div
      data-testid={`activity-event-${event.id}`}
      className="animate-fade-up flex items-baseline gap-3 border-b border-border/40 py-2 last:border-b-0"
    >
      <span className="font-mono text-[11px] uppercase tracking-wider text-primary">
        {event.type}
      </span>
      {event.task_id && (
        <span className="font-mono text-xs text-foreground">
          {event.task_id}
        </span>
      )}
      <span className="ml-auto shrink-0 text-[11px] text-muted-foreground">
        {formatRelative(event.ts)}
      </span>
    </div>
  );
}

function GitRow({ entry }: { entry: AgentGitActivity }) {
  const shortSha = entry.sha ? entry.sha.slice(0, 7) : undefined;
  return (
    <div
      data-testid={`activity-git-${entry.id}`}
      className="animate-fade-up flex items-baseline gap-3 border-b border-border/40 py-2 last:border-b-0"
    >
      <GitBranch className="h-3 w-3 shrink-0 text-primary" aria-hidden />
      <span className="font-mono text-xs text-foreground">{entry.repo}</span>
      <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] uppercase tracking-wider text-muted-foreground">
        {entry.action}
      </span>
      {entry.branch && (
        <span className="font-mono text-[11px] text-muted-foreground">
          {entry.branch}
        </span>
      )}
      {shortSha && (
        <span className="font-mono text-[11px] text-muted-foreground">
          {shortSha}
        </span>
      )}
      {entry.pr_number !== undefined && (
        <span className="font-mono text-[11px] text-primary">
          #{entry.pr_number}
        </span>
      )}
      {entry.message && (
        <span className="truncate text-xs text-muted-foreground">
          {entry.message}
        </span>
      )}
      <span className="ml-auto shrink-0 text-[11px] text-muted-foreground">
        {formatRelative(entry.ts)}
      </span>
    </div>
  );
}

export function ActivityPage() {
  const events = useQuery({
    queryKey: ["agent", "events"],
    queryFn: fetchEvents,
    refetchInterval: REFETCH_MS,
  });
  const git = useQuery({
    queryKey: ["agent", "git-activity"],
    queryFn: fetchGitActivity,
    refetchInterval: REFETCH_MS,
  });

  const eventList = events.data ?? [];
  const gitList = git.data ?? [];

  return (
    <div className="space-y-6 p-6" data-testid="activity-band">
      <header className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold">Activity</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted-foreground">
            Live agent events and git activity, streaming from the companion agent
            via the /agent proxy.
          </p>
        </div>
        <div data-testid="activity-heartbeat" className="shrink-0">
          <Heartbeat label="agent stream live" />
        </div>
      </header>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader className="flex-row items-center gap-2 space-y-0">
            <Activity className="h-4 w-4 text-primary" aria-hidden />
            <CardTitle className="text-base">Events</CardTitle>
          </CardHeader>
          <CardContent>
            {eventList.length === 0 ? (
              <EmptyState
                icon={Activity}
                title="No agent events yet"
                description="As the agent picks up work, events will stream into this feed."
              >
                <span data-testid="activity-events-empty" hidden />
              </EmptyState>
            ) : (
              <div className="divide-y divide-border/40">
                {eventList.map((event) => (
                  <EventRow key={event.id} event={event} />
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex-row items-center gap-2 space-y-0">
            <GitBranch className="h-4 w-4 text-primary" aria-hidden />
            <CardTitle className="text-base">Git activity</CardTitle>
          </CardHeader>
          <CardContent>
            {gitList.length === 0 ? (
              <EmptyState
                icon={GitBranch}
                title="No git activity yet"
                description="Pushes, PR opens, and merges from the agent will appear here."
              >
                <span data-testid="activity-git-empty" hidden />
              </EmptyState>
            ) : (
              <div className="divide-y divide-border/40">
                {gitList.map((entry) => (
                  <GitRow key={entry.id} entry={entry} />
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
