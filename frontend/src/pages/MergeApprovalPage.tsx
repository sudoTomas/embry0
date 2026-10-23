import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router";
import { toast } from "sonner";
import type { MergeApproval, MergeDecision, MergeDecisionResult } from "@/api/mergeApprovals";
import { isNotFound } from "@/api/errorDetail";
import { toastApiError } from "@/api/errorToast";
import { useDecideMergeApproval, useMergeApproval } from "@/hooks/useMergeApprovals";
import { AdminButton } from "@/components/auth/AdminButton";
import { Badge } from "@/components/ui/Badge";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { DashboardSkeleton } from "@/components/ui/PageSkeleton";
import { PageError } from "@/components/PageError";
import { CHECKS_TONES, VERDICT_LABELS, VERDICT_TONES, approvalTitle, decidedVia, detailSentence } from "@/lib/mergeApprovals";
import { sentenceCase, statusLabel, statusTone } from "@/lib/status";
import { formatDateTime } from "@/lib/utils";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2 rounded-lg border border-border bg-card p-4">
      <h2 className="text-section font-semibold">{title}</h2>
      {children}
    </section>
  );
}

function Lines({ items, className }: { items: string[]; className?: string }) {
  if (items.length === 0) return null;
  return (
    <ul className={className ?? "list-disc space-y-1 pl-5 text-sm text-fg-secondary"}>
      {items.map((item) => (
        <li key={item}>{item}</li>
      ))}
    </ul>
  );
}

/** Answers that left the ask pending, with the reason in `detail`. */
const KEPT_PENDING = new Set(["refused", "stale", "failed"]);
/** Answers that settled the ask the way the person asked. */
const SETTLED = new Set(["merged", "would_merge", "approved", "rejected"]);

/** The decision's answer as a toast: a warning with the reason when the ask stays pending, a success when it settled, a note otherwise (already decided). */
function toastOutcome(title: string, result: MergeDecisionResult) {
  const summary = `${title}: ${sentenceCase(result.outcome)}`;
  if (KEPT_PENDING.has(result.outcome)) {
    toast.warning(result.detail ? `${summary}. ${detailSentence(result.detail)}` : summary);
  } else if (SETTLED.has(result.outcome)) {
    toast.success(summary);
  } else {
    toast.info(summary);
  }
}

function Decision({ row }: { row: MergeApproval }) {
  const [asking, setAsking] = useState<MergeDecision | null>(null);
  const decide = useDecideMergeApproval();
  const title = approvalTitle(row);

  if (row.status !== "pending") {
    return (
      <p className="text-sm text-fg-secondary" data-testid="merge-approval-decided">
        {row.decided_by
          ? `Decided by ${row.decided_by} ${decidedVia(row.chat_id)}${row.decided_at ? ` on ${formatDateTime(row.decided_at)}` : ""}.`
          : `${statusLabel(row.status)}.`}
        {row.detail && ` ${detailSentence(row.detail)}`}
      </p>
    );
  }

  const submit = (decision: MergeDecision) => {
    decide.mutate(
      { id: row.id, decision },
      {
        onSuccess: (result) => {
          setAsking(null);
          toastOutcome(title, result);
        },
        onError: (e) => {
          setAsking(null);
          toastApiError(e, "The decision was not recorded");
        },
      },
    );
  };

  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <AdminButton type="button" onClick={() => setAsking("approve")} disabled={decide.isPending}>
          Approve
        </AdminButton>
        <AdminButton type="button" variant="outline" onClick={() => setAsking("reject")} disabled={decide.isPending}>
          Reject
        </AdminButton>
        {row.dry_run && <span className="text-sm text-fg-muted">Dry run: an approval is recorded and nothing merges.</span>}
      </div>
      {row.detail && (
        <p className="text-sm text-fg-secondary" data-testid="merge-approval-detail">
          {detailSentence(row.detail)}
        </p>
      )}
      {asking === "approve" && (
        <ConfirmDialog
          title={`Approve ${title}?`}
          confirmLabel={row.dry_run ? "Approve" : "Approve and merge"}
          pending={decide.isPending}
          onConfirm={() => submit("approve")}
          onClose={() => setAsking(null)}
        >
          {row.dry_run
            ? "Records the approval. In dry run nothing merges."
            : "Re-checks the pull request (same head, checks green, verification not blocking) and merges the pull request."}
        </ConfirmDialog>
      )}
      {asking === "reject" && (
        <ConfirmDialog
          title={`Reject ${title}?`}
          confirmLabel="Reject the ask"
          destructive
          pending={decide.isPending}
          onConfirm={() => submit("reject")}
          onClose={() => setAsking(null)}
        >
          The pull request stays open and embry0 does not merge it. A new ask needs a new push.
        </ConfirmDialog>
      )}
    </>
  );
}

/** One merge ask (spec 3.7): what embry0 found, what it recommends, and the decision. */
export function MergeApprovalPage() {
  const approvalId = useParams().approvalId ?? "";
  const ask = useMergeApproval(approvalId || null);

  if (ask.isError) {
    if (isNotFound(ask.error)) {
      return <PageError message={`No merge approval ${approvalId}`} back={{ to: "/merge-approvals", label: "All merge approvals" }} />;
    }
    return <PageError message={`Failed to load merge approval ${approvalId}`} onRetry={() => void ask.refetch()} />;
  }
  if (ask.isPending || !ask.data) return <DashboardSkeleton />;

  const row = ask.data;
  const a = row.ask;
  const recommendation = a?.recommendation;
  const verification = a?.verification;

  return (
    <div className="space-y-6" data-testid="merge-approval-page">
      <div className="space-y-2">
        <Link to="/merge-approvals" className="text-sm text-fg-muted hover:underline">
          ← Merge approvals
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="title-pixel text-xl font-bold">{approvalTitle(row)}</h1>
          <Badge tone={statusTone(row.status)}>{statusLabel(row.status)}</Badge>
          {row.dry_run && <Badge tone="neutral">Dry run</Badge>}
          {recommendation && <Badge tone={VERDICT_TONES[recommendation.verdict]}>{VERDICT_LABELS[recommendation.verdict]}</Badge>}
        </div>
        {a && <p className="text-sm text-fg">{a.title}</p>}
        <p className="text-sm text-fg-secondary">
          <a href={row.pr_url} target="_blank" rel="noreferrer" className="hover:text-primary hover:underline">
            Open the pull request
          </a>
          {row.job_id && (
            <>
              {" · job "}
              <Link to={`/jobs/${row.job_id}`} className="font-mono text-xs hover:text-primary hover:underline">
                {row.job_id}
              </Link>
            </>
          )}
          {` · asked ${formatDateTime(row.posted_at ?? row.created_at)}`}
        </p>
        <Decision row={row} />
      </div>

      {a ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <Section title="Summary">
            <p className="text-sm text-fg-secondary">{a.summary || "No summary."}</p>
            <p className="text-sm text-fg-secondary">
              <span className="font-mono text-xs">{`${a.head_ref} → ${a.base_ref}`}</span>
              {` · +${a.additions} −${a.deletions} in ${a.changed_files} ${a.changed_files === 1 ? "file" : "files"}`}
            </p>
          </Section>

          <Section title="Recommendation">
            {recommendation ? (
              <>
                <Badge tone={VERDICT_TONES[recommendation.verdict]}>{VERDICT_LABELS[recommendation.verdict]}</Badge>
                <Lines items={recommendation.reasons} />
                <Lines items={recommendation.positives} className="list-disc space-y-1 pl-5 text-sm text-fg-muted" />
                <Lines items={recommendation.notes} className="space-y-1 text-xs text-fg-muted" />
              </>
            ) : (
              <p className="text-sm text-fg-muted">This ask carries no recommendation.</p>
            )}
          </Section>

          <Section title="Checks">
            {a.checks_state ? (
              <p className="flex flex-wrap items-center gap-2 text-sm text-fg-secondary">
                <Badge tone={CHECKS_TONES[a.checks_state]}>{sentenceCase(a.checks_state)}</Badge>
                <span>{a.checks}</span>
              </p>
            ) : (
              <p className="text-sm text-fg-muted">The CI state was not recorded.</p>
            )}
            {verification?.present ? (
              <p className="text-sm text-fg-secondary">
                {`Verification gate: ${sentenceCase(verification.gate_verdict ?? "unknown")}`}
                {verification.blocking && ", blocking"}
                {verification.human_merge_only && ", human merge only"}
                {verification.for_this_head === false && " (for an earlier head)"}
                {"."}
              </p>
            ) : (
              <p className="text-sm text-fg-muted">No verification record for this job.</p>
            )}
            {verification?.human_merge_reasons && <Lines items={verification.human_merge_reasons} />}
          </Section>

          <Section title="Review">
            {a.review ? (
              <>
                <p className="text-sm text-fg-secondary">
                  {`${sentenceCase(a.review.decision || "no decision")} · ${a.review.critical} critical, ${a.review.important} important`}
                  {a.review.addresses_failure && ` · addresses the failure: ${sentenceCase(a.review.addresses_failure)}`}
                  {a.review.duplicate_pr !== null && ` · duplicate of #${a.review.duplicate_pr}`}
                </p>
                {a.review.summary && <p className="text-sm text-fg-secondary">{a.review.summary}</p>}
              </>
            ) : (
              <p className="text-sm text-fg-muted">No reviewer verdict on this pull request.</p>
            )}
          </Section>

          {Object.keys(a.denylisted).length > 0 && (
            <Section title="Denylisted paths">
              {Object.entries(a.denylisted).map(([category, hit]) => (
                <div key={category} className="space-y-1">
                  <p className="text-sm text-fg-secondary">{`${sentenceCase(category)} · ${hit.count}`}</p>
                  <ul className="space-y-0.5 font-mono text-xs text-fg-muted">
                    {hit.paths.map((path) => (
                      <li key={path}>{path}</li>
                    ))}
                  </ul>
                </div>
              ))}
            </Section>
          )}
        </div>
      ) : (
        <p className="text-sm text-fg-muted">This ask was recorded without its details.</p>
      )}
    </div>
  );
}
