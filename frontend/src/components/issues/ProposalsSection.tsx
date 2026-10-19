import { isAxiosError } from "axios";
import { formatDistanceToNow } from "date-fns";
import { ExternalLink } from "lucide-react";
import { Link } from "react-router";
import { toast } from "sonner";
import { Button } from "@/components/ui/Button";
import { useAcceptProposal, useProposals, useRejectProposal } from "@/hooks/useProposals";
import type { HealingCase } from "@/lib/types";

/**
 * Trackers whose drafts the API accepts (POST /cases/{id}/accept). The others —
 * GitLab, today — are accepted by a label in the tracker itself, so their rows
 * link there instead.
 */
const ACCEPTED_HERE = new Set(["github", "linear"]);

/**
 * Trackers where accepting writes to the ticket: the API puts the trigger label
 * on it before dispatching. The person is told before they confirm.
 */
const LABELLED_ON_ACCEPT = new Set(["linear"]);

const TRACKER_NAMES: Record<string, string> = { linear: "Linear", github: "GitHub", gitlab: "GitLab" };

function trackerName(kind: string | undefined): string {
  return (kind && TRACKER_NAMES[kind]) || kind || "its tracker";
}

function detailOf(error: unknown, fallback: string): string {
  const detail = isAxiosError(error) ? error.response?.data?.detail : undefined;
  return typeof detail === "string" ? detail : fallback;
}

/**
 * An organization's need (ADR 0011) waits here too. It has no ticket and is
 * never accepted into a fix: it is answered on the organization page, and
 * dismissing it here is the same answer as dismissing it there.
 */
function isOrgNeed(proposal: HealingCase): boolean {
  return proposal.origin === "org";
}

/** A need is filed on its repository, or on the bare owner when it covers several (or none). */
function ownerOf(proposal: HealingCase): string {
  return proposal.repo.split("/")[0];
}

/** Whether the case is filed on a bare owner rather than a repository. */
function onOwner(proposal: HealingCase): boolean {
  return !proposal.repo.includes("/");
}

/** When the draft was filed — for a finding that waited for a slot, later than when it was detected. */
function filedAt(proposal: HealingCase): string {
  return proposal.timestamps.tracked_at ?? proposal.timestamps.detected_at;
}

/**
 * What waits for a person: draft tickets the watcher filed that nobody has
 * answered, and organization needs. They are healing cases, not issues — an
 * issue exists only once a draft is accepted — so they sit above the issue
 * list rather than in it. Renders nothing when there are none: the Issues page
 * of a deployment with no watcher and no organization is unchanged.
 */
export function ProposalsSection() {
  const proposals = useProposals();
  const accept = useAcceptProposal();
  const reject = useRejectProposal();

  if (proposals.isError) {
    return (
      <div role="alert" className="rounded-lg border border-amber-400/20 px-4 py-3 text-sm text-muted-foreground">
        Could not load the watcher&apos;s proposals.{" "}
        <button className="text-primary hover:underline" onClick={() => proposals.refetch()}>
          Retry
        </button>
      </div>
    );
  }
  const rows = proposals.data?.cases ?? [];
  if (rows.length === 0) return null;

  const handleAccept = (proposal: HealingCase) => {
    const tracker = proposal.tracker;
    const effect =
      tracker && LABELLED_ON_ACCEPT.has(tracker.kind)
        ? `This adds the trigger label to ${tracker.key} in ${trackerName(tracker.kind)} and starts the fix pipeline on ${proposal.repo}.`
        : `This starts the fix pipeline on ${proposal.repo}.`;
    if (!confirm(`Accept "${proposal.title}"? ${effect}`)) return;
    accept.mutate(proposal.id, {
      onSuccess: (result) => toast.success(`Accepted — job ${result.job_id.slice(0, 12)} started`),
      onError: (e) => toast.error(detailOf(e, "Could not accept the proposal")),
    });
  };

  const handleDismiss = (proposal: HealingCase) => {
    const owner = ownerOf(proposal);
    if (!confirm(`Dismiss "${proposal.title}"? It stays dismissed on the ${owner} needs list until someone reopens it.`))
      return;
    reject.mutate(proposal.id, {
      onSuccess: () => toast.success(`Dismissed. Reopen it from the ${owner} needs list.`),
      onError: (e) => toast.error(detailOf(e, "Could not dismiss the need")),
    });
  };

  const handleReject = (proposal: HealingCase) => {
    const ticket = proposal.tracker?.key ?? "the ticket";
    const where = trackerName(proposal.tracker?.kind);
    if (
      !confirm(
        `Reject "${proposal.title}"? The watcher stops waiting on it. ${ticket} stays open in ${where} — close it there too.`,
      )
    )
      return;
    reject.mutate(proposal.id, {
      onSuccess: () => toast.success(`Rejected. ${ticket} is still open in ${where}.`),
      onError: (e) => toast.error(detailOf(e, "Could not reject the proposal")),
    });
  };

  const busy = accept.isPending || reject.isPending;

  return (
    <section aria-labelledby="proposals-heading" className="space-y-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 id="proposals-heading" className="text-section font-semibold">
          Proposals
        </h2>
        <span className="inline-flex items-center rounded bg-amber-dim px-1.5 py-0.5 text-2xs font-semibold tabular-nums text-warning">
          {proposals.data?.total ?? rows.length}
        </span>
        <p className="text-sm text-muted-foreground">
          Draft tickets the watcher filed, and what organizations still need. Nothing runs until one is accepted.
        </p>
      </div>

      <div className="overflow-x-auto rounded-lg border border-amber-400/20">
        <table className="data-table">
          <thead>
            <tr>
              <th>Proposal</th>
              <th className="hidden sm:table-cell">Ticket</th>
              <th className="hidden sm:table-cell">Waiting</th>
              <th className="text-right">Decision</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((proposal) => {
              const tracker = proposal.tracker;
              const filed = filedAt(proposal);
              return (
                <tr key={proposal.id}>
                  {/* The one flexible column: it takes what the others leave and
                      truncates, so the decision stays on screen without a
                      sideways scroll. The repo rides under the title. Below sm
                      the ticket and waiting columns step aside and the decision
                      wraps, so Accept and Reject stay on a phone screen. */}
                  <td className="w-full min-w-[7rem] max-w-0 sm:min-w-[14rem]">
                    <div className="truncate" title={proposal.title}>
                      {proposal.title}
                    </div>
                    {isOrgNeed(proposal) && onOwner(proposal) ? (
                      // A bare owner is no repository: it links to its organization.
                      <Link
                        to={`/orgs/${encodeURIComponent(proposal.repo)}`}
                        className="block truncate font-mono text-xs text-muted-foreground hover:underline"
                      >
                        {proposal.repo}
                      </Link>
                    ) : (
                      <div className="truncate font-mono text-xs text-muted-foreground">{proposal.repo}</div>
                    )}
                  </td>
                  <td className="hidden whitespace-nowrap sm:table-cell">
                    {isOrgNeed(proposal) ? (
                      <span className="text-muted-foreground">Organization need</span>
                    ) : tracker?.url ? (
                      <a
                        href={tracker.url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-primary hover:underline"
                      >
                        {tracker.key}
                      </a>
                    ) : (
                      // Cases filed before the url was stored (#378).
                      <span>{tracker?.key ?? "—"}</span>
                    )}
                  </td>
                  <td
                    className="hidden whitespace-nowrap text-muted-foreground sm:table-cell"
                    title={new Date(filed).toLocaleString()}
                  >
                    {formatDistanceToNow(new Date(filed))}
                  </td>
                  <td className="sm:whitespace-nowrap">
                    <div className="flex flex-wrap items-center justify-end gap-x-2 gap-y-1 whitespace-nowrap sm:flex-nowrap">
                      {isOrgNeed(proposal) ? (
                        <Link
                          to={`/orgs/${encodeURIComponent(ownerOf(proposal))}`}
                          className="text-xs text-primary hover:underline"
                        >
                          Answer on {ownerOf(proposal)}
                        </Link>
                      ) : tracker && ACCEPTED_HERE.has(tracker.kind) ? (
                        <Button size="xs" disabled={busy} onClick={() => handleAccept(proposal)}>
                          Accept
                        </Button>
                      ) : tracker?.url ? (
                        <a
                          href={tracker.url}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                        >
                          Accept by label in {trackerName(tracker.kind)}
                          <ExternalLink className="h-3 w-3" />
                        </a>
                      ) : (
                        <span className="text-xs text-muted-foreground">
                          Accept by label in {trackerName(tracker?.kind)}
                        </span>
                      )}
                      {isOrgNeed(proposal) ? (
                        <Button size="xs" variant="outline" disabled={busy} onClick={() => handleDismiss(proposal)}>
                          Dismiss
                        </Button>
                      ) : (
                        <Button size="xs" variant="outline" disabled={busy} onClick={() => handleReject(proposal)}>
                          Reject
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </section>
  );
}
