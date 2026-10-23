import { ClipboardList } from "lucide-react";
import type { OrgNeeds } from "@/api/orgs";
import { PageError } from "@/components/PageError";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { statusLabel } from "@/lib/status";
import { groupNeeds } from "@/lib/orgs";

/** Shown wherever the needs list would be, when the server has no needs endpoint yet. */
export function NeedsEndpointMissing() {
  return (
    <EmptyState
      icon={ClipboardList}
      title="This server has no needs endpoint yet"
      description="Once embry0 is upgraded, this lists what it still needs to maintain each repository. The repositories table shows the same facts meanwhile."
      className="py-10"
    />
  );
}

/**
 * What embry0 still needs, grouped by kind, each group with how many needs
 * and how many distinct repositories it covers.
 */
export function OrgNeedsList({
  needs,
  isLoading,
  isError,
  onRetry,
}: {
  needs: OrgNeeds | null | undefined;
  isLoading: boolean;
  isError: boolean;
  onRetry?: () => void;
}) {
  if (isError) return <PageError message="Failed to load the needs list" onRetry={onRetry} />;
  if (isLoading || needs === undefined) return <Skeleton className="h-24 w-full" />;
  if (needs === null) return <NeedsEndpointMissing />;
  if (needs.needs.length === 0) {
    return (
      <EmptyState
        icon={ClipboardList}
        title="Nothing needed"
        description="Discovery found nothing embry0 still needs from these repositories."
        className="py-10"
      />
    );
  }

  const groups = groupNeeds(needs.needs);
  return (
    <div className="space-y-2" data-testid="org-needs">
      <p className="text-xs text-muted-foreground" data-testid="org-needs-counts">
        {needs.counts.open} open, {needs.counts.done} done, {needs.counts.dismissed} dismissed
      </p>
      <ul className="divide-y divide-white/[0.04] rounded-lg border border-white/[0.06]">
        {groups.map((g) => (
          <li key={g.kind} data-testid="org-need-group" data-kind={g.kind}>
            <details>
              <summary className="flex cursor-pointer flex-wrap items-center gap-2 px-3 py-2 text-sm">
                <span className="font-medium">{g.label}</span>
                <span className="text-xs text-muted-foreground" data-testid="org-need-group-counts">
                  {g.needs.length} {g.needs.length === 1 ? "need" : "needs"}, {g.repoCount}{" "}
                  {g.repoCount === 1 ? "repository" : "repositories"}
                </span>
                {g.openCount > 0 && <Badge tone="neutral">{g.openCount} open</Badge>}
              </summary>
              <ul className="space-y-2 px-3 pb-3">
                {g.needs.map((n) => (
                  <li key={n.id} className="min-w-0 text-sm">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className={n.status === "open" ? "" : "text-white/40"}>{n.title}</span>
                      {n.status !== "open" && <Badge tone="neutral">{statusLabel(n.status)}</Badge>}
                    </div>
                    {n.detail && <p className="text-xs text-muted-foreground">{n.detail}</p>}
                    {n.repos.length > 0 && (
                      <p className="break-words font-mono text-xs text-white/50">{n.repos.join(", ")}</p>
                    )}
                  </li>
                ))}
              </ul>
            </details>
          </li>
        ))}
      </ul>
    </div>
  );
}
