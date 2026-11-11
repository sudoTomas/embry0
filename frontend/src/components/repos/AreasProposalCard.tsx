import { Link } from "react-router";
import { toast } from "sonner";
import type { AreasProposal } from "@/api/areas";
import { formatErrorDetail } from "@/api/errorDetail";
import { toastApiError } from "@/api/errorToast";
import { TextDiff } from "@/components/documents/TextDiff";
import { IssueBody } from "@/components/issues/IssueBody";
import { Button } from "@/components/ui/Button";
import { useDecideAreasProposal } from "@/hooks/useRepoAreas";
import { parseUnifiedDiff } from "@/lib/textDiff";
import { AreaChangeList } from "./AreaChangeList";

const HEADING = "embry0 mapped this repository's areas. Nothing changes until you apply them.";
const STALE = "Someone changed these areas after the map started, so it cannot be applied. Map again for a fresh one.";

/** What a map that is not waiting for a person says. */
const STATUS_WORDS: Record<string, string> = {
  running: "embry0 is mapping this repository's areas.",
  applied: "The last map was applied.",
  rejected: "The last map was rejected.",
  stale: "The last map was retired: the areas changed, or a newer map started.",
  unchanged: "The last map found the areas embry0 already keeps.",
  failed: "The last map did not finish.",
};

/**
 * A map of the repository's areas (spec 2026-11-10, ADR 0019): what applying it changes, area by area with the
 * owners it adds or removes first, the agent's notes and the text diff, with Apply and Reject. An apply is
 * refused when the areas changed after the map started; History can undo an applied map.
 * `onEdit` opens the editor on the map's document, to change it before saving.
 */
export function AreasProposalCard({
  repo,
  proposal,
  onEdit,
  canDecide = true,
}: {
  repo: string;
  proposal: AreasProposal;
  onEdit: (text: string) => void;
  /** Whether this session may apply or reject it (an operate key); without, the card is read only. */
  canDecide?: boolean;
}) {
  const decide = useDecideAreasProposal(repo);

  function act(decision: "apply" | "reject") {
    decide.mutate(
      { id: proposal.id, decision },
      {
        onSuccess: () =>
          toast.success(decision === "apply" ? "Areas applied. History can undo it." : "Map rejected; the areas stay."),
        onError: (error) => {
          const response = (error as { response?: { status?: number; data?: { detail?: unknown } } }).response;
          if (response?.status === 409) toast.error(formatErrorDetail(response.data?.detail) ?? STALE);
          else toastApiError(error, decision === "apply" ? "Could not apply the map" : "Could not reject the map");
        },
      },
    );
  }

  const job = proposal.job_id ? (
    <Link className="underline hover:text-fg" to={`/jobs/${proposal.job_id}`}>
      {proposal.job_id}
    </Link>
  ) : null;

  if (proposal.status !== "pending") {
    return (
      <div className="space-y-1 text-xs text-fg-muted" data-testid="areas-proposal" data-status={proposal.status}>
        <p>
          {STATUS_WORDS[proposal.status] ?? proposal.status}
          {job && <> · {job}</>}
        </p>
        {proposal.status === "failed" && proposal.failure && (
          <p className="text-warning [overflow-wrap:anywhere]" data-testid="areas-proposal-failure">
            {proposal.failure}
          </p>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-3 rounded-lg border border-primary/40 p-3" data-testid="areas-proposal" data-status="pending">
      <p className="text-sm font-medium">{HEADING}</p>
      {proposal.stale && (
        <p role="alert" className="text-sm text-warning" data-testid="areas-proposal-stale">
          {STALE}
        </p>
      )}
      <AreaChangeList changes={proposal.changes} />
      {proposal.notes_md && (
        <details className="rounded-md border border-border px-3 py-2 text-sm">
          <summary className="cursor-pointer text-fg-secondary hover:text-fg">What the agent found</summary>
          <div className="mt-2">
            <IssueBody body={proposal.notes_md} />
          </div>
        </details>
      )}
      {proposal.text_diff && (
        <details className="rounded-md border border-border px-3 py-2 text-sm">
          <summary className="cursor-pointer text-fg-secondary hover:text-fg">Text changes</summary>
          <div className="mt-2">
            <TextDiff lines={parseUnifiedDiff(proposal.text_diff)} label="Text changes" />
          </div>
        </details>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {canDecide && (
          <>
            <Button size="sm" disabled={decide.isPending || proposal.stale} onClick={() => act("apply")}>
              Apply
            </Button>
            <Button size="sm" variant="secondary" disabled={decide.isPending} onClick={() => act("reject")}>
              Reject
            </Button>
          </>
        )}
        {canDecide && proposal.proposed_text && (
          <Button size="sm" variant="ghost" onClick={() => onEdit(proposal.proposed_text ?? "")}>
            Change it first
          </Button>
        )}
        {job && <span className="text-xs text-fg-muted">From {job}</span>}
      </div>
    </div>
  );
}
