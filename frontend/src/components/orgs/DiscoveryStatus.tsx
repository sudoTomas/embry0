import { useEffect, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { DiscoveryRun } from "@/api/orgs";
import { Badge } from "@/components/ui/Badge";
import { SUMMARY_FIELDS } from "@/lib/orgs";
import { formatDate } from "@/lib/utils";

/**
 * The newest discovery run: live progress while it runs, the summary once
 * it is done, the error when it failed. When a run finishes, everything the
 * run wrote (repositories, needs, the organization row) is read again.
 */
export function DiscoveryStatus({ owner, run }: { owner: string; run: DiscoveryRun | null | undefined }) {
  const qc = useQueryClient();
  const previous = useRef<string | undefined>(undefined);
  const status = run ? `${run.id}:${run.status}` : undefined;

  useEffect(() => {
    const was = previous.current;
    previous.current = status;
    if (was && status && was !== status && was.endsWith(":running")) {
      void qc.invalidateQueries({ queryKey: ["orgs"] });
    }
  }, [status, qc]);

  if (!run) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="discovery-none">
        Discovery has not run for {owner} yet.
      </p>
    );
  }

  if (run.status === "running") {
    const total = run.repos_total ?? 0;
    const seen = run.repos_seen ?? 0;
    const pct = total > 0 ? Math.min(100, Math.round((seen / total) * 100)) : 0;
    return (
      <div className="space-y-2" data-testid="discovery-running">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Badge tone="gold">Running</Badge>
          <span data-testid="discovery-progress">
            {total > 0 ? `${seen} of ${total} repositories` : "listing repositories"}
          </span>
        </div>
        <div
          className="h-1.5 w-full overflow-hidden rounded-full bg-white/[0.06]"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={total || undefined}
          aria-valuenow={seen}
          aria-label="Discovery progress"
        >
          <div className="h-full bg-primary transition-all" style={{ width: `${pct}%` }} />
        </div>
      </div>
    );
  }

  if (run.status === "error") {
    return (
      <div className="space-y-1" data-testid="discovery-error">
        <Badge tone="error">Failed</Badge>
        <p className="text-sm text-destructive">{run.error ?? "Discovery failed."}</p>
      </div>
    );
  }

  return (
    <div className="space-y-2" data-testid="discovery-done">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge tone="success">Done</Badge>
        {run.finished_at && <span className="text-muted-foreground">finished {formatDate(run.finished_at)}</span>}
      </div>
      <dl className="grid grid-cols-3 gap-2 sm:grid-cols-5" data-testid="discovery-summary">
        {SUMMARY_FIELDS.filter((f) => typeof run.summary[f.key] === "number").map((f) => (
          <div key={f.key} className="min-w-0 rounded-md border border-white/[0.06] px-2.5 py-1.5">
            <dt className="text-xs uppercase tracking-wider text-muted-foreground">{f.label}</dt>
            <dd className="font-mono text-base">{run.summary[f.key]}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
