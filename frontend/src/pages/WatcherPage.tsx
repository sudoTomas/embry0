import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router";
import { RadioTower, Play } from "lucide-react";
import { toast } from "sonner";
import { useWatcherRuns } from "@/hooks/useWatcherRuns";
import { triggerWatcherRun } from "@/api/watcher";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { WatcherStacksTab } from "@/components/watcher/WatcherStacksTab";
import { formatDateTime, formatTime } from "@/lib/utils";
import { watcherActionLabel, watcherActionTone } from "@/lib/watcherActions";
import { WATCHER_TABS, watcherTab } from "@/lib/watcherTabs";
import { toastApiError } from "@/api/errorToast";
import type { RunWith } from "@/api/runWith";
import { RunWithControl } from "@/components/runwith/RunWithControl";


function windowLabel(startIso: string, endIso: string): string {
  return `${formatTime(startIso)}–${formatTime(endIso)}`;
}

/**
 * Watcher (RAV-1058 increment 2, exposes RAV-657): audit trail of the
 * log-watcher/proposer ticks + a manual "Run tick now". A tick that finds
 * signal runs an analysis job inline, so the manual trigger can take
 * minutes — the button stays in its running state until the verdict lands.
 * Proposals stay behind the human gate (Linear drafts), by design.
 */
export function WatcherPage() {
  const runs = useWatcherRuns(50);
  const queryClient = useQueryClient();

  // The open tab lives in the URL so a stack can be linked to.
  const [params, setParams] = useSearchParams();
  const tab = watcherTab(params.get("tab")).value;
  const openTab = (value: string) => setParams(value === WATCHER_TABS[0].value ? {} : { tab: value }, { replace: true });

  const [runWith, setRunWith] = useState<RunWith>({});
  const tick = useMutation({
    mutationFn: () => triggerWatcherRun(runWith),
    onSuccess: (result) => {
      const label = watcherActionLabel(result.action);
      if (result.action === "proposed") {
        toast.success(`Tick finished: ${label} — ${result.tracker_key ?? "draft created"}`);
      } else if (result.action === "error") {
        toast.error(`Tick finished with error`);
      } else {
        toast.info(`Tick finished: ${label}`);
      }
      queryClient.invalidateQueries({ queryKey: ["watcher-runs"] });
    },
    onError: (e) => toastApiError(e, "Watcher tick failed"),
  });

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="title-pixel text-xl font-bold">Watcher</h1>
        <Button disabled={tick.isPending} onClick={() => tick.mutate()}>
          <Play className="mr-2 h-4 w-4" />
          {tick.isPending ? "Running tick… (can take minutes)" : "Run tick now"}
        </Button>
      </div>
      {/* Every watched stack ticks, so only the keys every repository may use are offered. */}
      <RunWithControl value={runWith} onChange={setRunWith} />

      <p className="text-sm text-muted-foreground">
        Each tick reads the last log window from Loki, and — when there&apos;s
        enough signal — runs an analysis job that may propose a Linear draft
        issue. Proposals are human-gated: nothing auto-runs from here.
      </p>

      <Tabs defaultValue={WATCHER_TABS[0].value} value={tab} onValueChange={openTab}>
        <TabsList>
          {WATCHER_TABS.map((t) => (
            <TabsTrigger key={t.value} value={t.value}>
              {t.label}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="ticks">
          {runs.isError ? (
            <PageError message="Failed to load watcher runs" onRetry={() => runs.refetch()} />
          ) : runs.isPending ? (
            <TableSkeleton />
          ) : runs.data.length === 0 ? (
            <EmptyState
              icon={RadioTower}
              title="No watcher ticks recorded yet"
              description="Tick results land here when the scheduled watcher runs or you trigger one manually."
            />
          ) : (
            // Below sm the window, log count, proposal and detail step aside, so
            // when, what happened and the analysis job (the row's link) fit a
            // phone on one line per tick.
            <div className="overflow-x-auto rounded-lg border border-border">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>When</th>
                    <th>Action</th>
                    <th className="hidden sm:table-cell">Window</th>
                    <th className="hidden sm:table-cell">Log lines</th>
                    <th>Analysis job</th>
                    <th className="hidden sm:table-cell">Proposal</th>
                    <th className="hidden sm:table-cell">Detail</th>
                  </tr>
                </thead>
                <tbody>
                  {runs.data.map((run) => (
                    <tr key={run.id}>
                      <td className="whitespace-nowrap text-muted-foreground">
                        {formatDateTime(run.created_at)}
                      </td>
                      <td>
                        <Badge tone={watcherActionTone(run.action)}>
                          {watcherActionLabel(run.action)}
                        </Badge>
                      </td>
                      <td className="hidden whitespace-nowrap text-xs text-muted-foreground sm:table-cell">
                        {windowLabel(run.window_start, run.window_end)}
                      </td>
                      <td className="hidden tabular-nums text-muted-foreground sm:table-cell">{run.log_lines}</td>
                      <td className="whitespace-nowrap">
                        {run.job_id ? (
                          <Link to={`/jobs/${run.job_id}`} className="font-mono text-xs text-fg hover:text-primary hover:underline">
                            {run.job_id.slice(0, 12)}
                          </Link>
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </td>
                      <td className="hidden whitespace-nowrap sm:table-cell">
                        {run.tracker_key ? (
                          run.tracker_url ? (
                            <a
                              href={run.tracker_url}
                              target="_blank"
                              rel="noreferrer"
                              className="text-fg hover:text-primary hover:underline"
                            >
                              {run.tracker_key}
                            </a>
                          ) : (
                            // Rows filed before the url was stored (#378).
                            <span>{run.tracker_key}</span>
                          )
                        ) : (
                          <span className="text-muted-foreground">—</span>
                        )}
                      </td>
                      <td className="hidden max-w-xs truncate text-muted-foreground sm:table-cell" title={run.detail ?? undefined}>
                        {run.detail ?? "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </TabsContent>
        <TabsContent value="stacks">
          <WatcherStacksTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
