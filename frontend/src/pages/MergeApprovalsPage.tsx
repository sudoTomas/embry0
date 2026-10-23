import { useState } from "react";
import { Link } from "react-router";
import { GitMerge } from "lucide-react";
import { MERGE_APPROVAL_STATUSES, type MergeApproval, type MergeApprovalStatus } from "@/api/mergeApprovals";
import { useMergeApprovals } from "@/hooks/useMergeApprovals";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/EmptyState";
import { FilterSelect } from "@/components/ui/FilterSelect";
import { PageError } from "@/components/PageError";
import { TableSkeleton } from "@/components/TableSkeleton";
import { CHECKS_TONES, VERDICT_LABELS, VERDICT_TONES, approvalTitle } from "@/lib/mergeApprovals";
import { sentenceCase, statusLabel, statusTone } from "@/lib/status";
import { formatDateTime } from "@/lib/utils";

function RecommendationBadge({ row }: { row: MergeApproval }) {
  const verdict = row.ask?.recommendation?.verdict;
  if (!verdict) return <span className="text-fg-muted">—</span>;
  return <Badge tone={VERDICT_TONES[verdict]}>{VERDICT_LABELS[verdict]}</Badge>;
}

function ChecksBadge({ row }: { row: MergeApproval }) {
  const state = row.ask?.checks_state;
  if (!state) return <span className="text-fg-muted">—</span>;
  return <Badge tone={CHECKS_TONES[state]}>{sentenceCase(state)}</Badge>;
}

/**
 * Merge approvals (spec 3.7): the asks embry0 posted for a person to answer,
 * one status at a time, pending first. Each row leads to the ask's own page,
 * where the decision is made. A deployment with the feature off gets an empty
 * state, not an error: the route's 503 is read as "off".
 */
export function MergeApprovalsPage() {
  const [status, setStatus] = useState<MergeApprovalStatus | "all">("pending");
  const list = useMergeApprovals(status);

  let body;
  if (list.isError) {
    body = <PageError message="Failed to load merge approvals" onRetry={() => void list.refetch()} />;
  } else if (list.isPending) {
    body = <TableSkeleton />;
  } else if (list.data === null) {
    body = (
      <EmptyState
        icon={GitMerge}
        title="Merge approvals are off on this deployment"
        description="Set MERGE_APPROVAL_MODE and the approval chat in the root .env to have embry0 ask before it merges."
      />
    );
  } else if (list.data.items.length === 0) {
    body = (
      <EmptyState
        icon={GitMerge}
        title={status === "all" ? "No merge approvals yet" : `No ${statusLabel(status).toLowerCase()} merge approvals`}
        description="An ask appears here when a reviewed pull request waits for a person to approve its merge."
      />
    );
  } else {
    body = (
      // On a phone the checks, who decided and when step aside; the PR, its
      // title, the recommendation and the status stay.
      <div className="overflow-x-auto rounded-lg border border-border">
        <table className="data-table">
          <thead>
            <tr>
              <th scope="col">Pull request</th>
              <th scope="col">Title</th>
              <th scope="col">Recommendation</th>
              <th scope="col" className="hidden sm:table-cell">Checks</th>
              <th scope="col">Status</th>
              <th scope="col" className="hidden sm:table-cell">Decided by</th>
              <th scope="col" className="num hidden sm:table-cell">Asked</th>
              <th scope="col"><span className="sr-only">Details</span></th>
            </tr>
          </thead>
          <tbody>
            {list.data.items.map((row) => (
              <tr key={row.id} data-testid="merge-approval-row">
                <td className="whitespace-nowrap">
                  <a href={row.pr_url} target="_blank" rel="noreferrer" className="font-mono text-xs text-fg hover:text-primary hover:underline">
                    {approvalTitle(row)}
                  </a>
                </td>
                <td className="w-full max-w-0 truncate" title={row.ask?.title}>
                  {row.ask?.title || <span className="text-fg-muted">—</span>}
                </td>
                <td className="whitespace-nowrap"><RecommendationBadge row={row} /></td>
                <td className="hidden whitespace-nowrap sm:table-cell"><ChecksBadge row={row} /></td>
                <td className="whitespace-nowrap">
                  <span className="inline-flex items-center gap-1.5">
                    <Badge tone={statusTone(row.status)}>{statusLabel(row.status)}</Badge>
                    {row.dry_run && <Badge tone="neutral">Dry run</Badge>}
                  </span>
                </td>
                <td className="hidden whitespace-nowrap text-fg-secondary sm:table-cell">{row.decided_by ?? "—"}</td>
                <td className="num hidden whitespace-nowrap text-fg-muted sm:table-cell">{formatDateTime(row.posted_at ?? row.created_at)}</td>
                <td className="whitespace-nowrap">
                  <Link to={`/merge-approvals/${encodeURIComponent(row.id)}`} className="text-sm text-fg hover:text-primary hover:underline">
                    Details
                  </Link>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="title-pixel text-xl font-bold">Merge approvals</h1>
          <p className="text-sm text-muted-foreground">
            Reviewed pull requests waiting for a person to approve the merge, and how each was answered.
          </p>
        </div>
        <FilterSelect
          label="Statuses"
          value={status === "all" ? undefined : status}
          options={[...MERGE_APPROVAL_STATUSES]}
          onChange={(v) => setStatus((v as MergeApprovalStatus | undefined) ?? "all")}
          optionLabel={statusLabel}
        />
      </div>
      {list.data?.mode === "dry_run" && (
        <p className="text-sm text-fg-secondary" data-testid="merge-approvals-mode">
          This deployment is in dry run: decisions are recorded and nothing merges.
        </p>
      )}
      {body}
    </div>
  );
}
