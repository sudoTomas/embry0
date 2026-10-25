import { toast } from "sonner";
import { toastApiError } from "@/api/errorToast";
import type { InboxQuest } from "@/api/world";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { useDismissQuest, useRateQuest, useRunAgain } from "@/hooks/useWorld";
import {
  OUTCOME_WORDS,
  formatCost,
  inboxOrder,
  outcomeBadgeTone,
  outcomeTone,
  placeOf,
  quickActs,
  type Quick,
} from "@/lib/world";

const QUICK_LABEL: Record<Quick, string> = {
  again: "Run again",
  dismiss: "Dismiss",
  good: "Good",
};

/**
 * Finished jobs nobody reviewed yet: a failure or an open pull request
 * first. A card opens its report; a failure can run again, and finished
 * work can be rated or dismissed, right on the card.
 */
export function QuestInbox({
  quests,
  onOpen,
  canAct = false,
}: {
  quests: readonly InboxQuest[];
  onOpen: (jobId: string) => void;
  canAct?: boolean;
}) {
  const lookup = useIdentityLookup();
  const again = useRunAgain();
  const dismiss = useDismissQuest();
  const rate = useRateQuest();
  const busy = again.isPending || dismiss.isPending || rate.isPending;
  if (quests.length === 0) {
    return (
      <p
        className="text-sm text-muted-foreground"
        data-testid="quest-inbox"
        data-count={0}
      >
        Nothing waiting for review.
      </p>
    );
  }
  const act = (q: InboxQuest, which: Quick) => {
    const fail = (what: string) => (e: Error) => toastApiError(e, what);
    if (which === "again")
      again.mutate(
        { jobId: q.job_id },
        {
          onSuccess: (r) =>
            toast.success(`Running again as ${r.followup_job_id}.`),
          onError: fail("Not started again"),
        },
      );
    else if (which === "dismiss")
      dismiss.mutate(q.job_id, {
        onSuccess: () => toast.success("Dismissed."),
        onError: fail("Not dismissed"),
      });
    else
      rate.mutate(
        { jobId: q.job_id, rating: "good" },
        {
          onSuccess: () => toast.success("Rated good."),
          onError: fail("Not rated"),
        },
      );
  };
  return (
    <Card
      role="region"
      aria-labelledby="quest-inbox-title"
      data-testid="quest-inbox"
      data-count={quests.length}
    >
      <CardHeader className="flex-row items-center gap-2 space-y-0 pb-3">
        <h2 id="quest-inbox-title" className="text-section font-semibold">
          Finished, waiting for review
        </h2>
        <Badge tone="gold">{quests.length} to review</Badge>
      </CardHeader>
      <CardContent>
        <ul className="flex gap-2 overflow-x-auto pb-1">
          {inboxOrder(quests).map((q) => {
            const identity = lookup(q.leader);
            const quick = canAct ? quickActs(q) : [];
            return (
              <li
                key={q.job_id}
                className="flex w-64 shrink-0 flex-col rounded-md border border-border bg-white/[0.02] transition-colors hover:border-border-strong"
              >
                <button
                  type="button"
                  className="flex w-full flex-1 items-start gap-2.5 rounded-t-md p-2.5 text-left hover:bg-overlay"
                  onClick={() => onOpen(q.job_id)}
                  data-testid="inbox-quest"
                >
                  <AgentAvatar
                    identity={identity}
                    size={32}
                    state={
                      outcomeTone(q.outcome) === "loss" ? "failed" : "done"
                    }
                  />
                  <span className="min-w-0 flex-1 space-y-1">
                    <span className="block truncate text-sm text-fg">
                      {q.title}
                    </span>
                    <Badge tone={outcomeBadgeTone(q.outcome)}>
                      {OUTCOME_WORDS[q.outcome]}
                    </Badge>
                    <span className="block truncate text-2xs text-fg-muted">
                      {identity.displayName} · {placeOf(q.repo, q.area)} ·{" "}
                      {formatCost(q.cost_usd)}
                    </span>
                  </span>
                </button>
                <div className="flex gap-1.5 border-t border-border px-2.5 py-1.5">
                  {quick.map((which, i) => (
                    <Button
                      key={which}
                      size="xs"
                      variant={i === 0 ? "secondary" : "ghost"}
                      disabled={busy}
                      onClick={() => act(q, which)}
                      data-testid={`inbox-${which}`}
                    >
                      {QUICK_LABEL[which]}
                    </Button>
                  ))}
                  <Button
                    size="xs"
                    variant="ghost"
                    className="ml-auto"
                    onClick={() => onOpen(q.job_id)}
                    data-testid="inbox-review"
                  >
                    Review
                  </Button>
                </div>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}
