import { Link } from "react-router";
import { standardApiPath } from "@/api/standards";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { useOwnerStandards } from "@/hooks/useStandards";
import { modeCounts, scoreTextClass, standardRows } from "@/lib/standards";
import { cn, formatDate } from "@/lib/utils";
import { Fact, FactStrip, QUIET_LINK } from "./FactStrip";

/**
 * The organization page's line on one kind of standard: its active version,
 * how many repositories are held to it, and the lowest score, with the way
 * to the Standards view.
 */
export function OrgStandardSummary({ owner, kind = "design" }: { owner: string; kind?: string }) {
  const standards = useOwnerStandards(owner);
  const to = `/orgs/${owner}/standards`;

  if (standards.isLoading) return <Skeleton className="h-10 w-full" />;
  if (standards.isError || !standards.data) {
    return (
      <FactStrip data-testid="org-standard-summary">
        <span className="text-destructive">The standards did not load.</span>
        <Button type="button" size="xs" variant="outline" onClick={() => void standards.refetch()}>
          Retry
        </Button>
      </FactStrip>
    );
  }

  const active = standards.data.standards.find((s) => s.kind === kind) ?? null;
  // Only the stored states: a repository never set is in mode off and unscored.
  const rows = standardRows(owner, kind, [], standards.data.repos);
  const counts = modeCounts(rows);
  const lowest = rows.find((r) => r.state.score !== null) ?? null;

  return (
    <FactStrip data-testid="org-standard-summary">
      {active ? (
        <>
          <Fact label="Version" data-testid="org-standard-version">
            <span className="font-mono">{active.version}</span>
          </Fact>
          <Fact label="Written">{active.created_at ? formatDate(active.created_at) : "-"}</Fact>
          <Fact label="Held to it" data-testid="org-standard-held">
            {`${counts.advise + counts.enforce} ${counts.advise + counts.enforce === 1 ? "repository" : "repositories"}`}
          </Fact>
          <Fact label="Lowest score" data-testid="org-standard-lowest">
            {lowest ? (
              <>
                <span className={cn("font-mono font-semibold", scoreTextClass(lowest.state.score))}>{lowest.state.score}</span>
                <span className="text-fg-muted">{` ${lowest.repo.slice(lowest.repo.indexOf("/") + 1)}`}</span>
              </>
            ) : (
              <span className="text-fg-muted">no audit yet</span>
            )}
          </Fact>
        </>
      ) : (
        <span className="text-fg-muted" data-testid="org-standard-none">
          No {kind} standard yet. An admin writes one with <span className="font-mono text-xs">PUT {standardApiPath(owner, kind)}</span>.
        </span>
      )}
      <Link to={to} className={cn(QUIET_LINK, "sm:ml-auto")} data-testid="org-standard-link">
        Open the {kind} standard →
      </Link>
    </FactStrip>
  );
}
