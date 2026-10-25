import type { InboxQuest } from "@/api/world";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { Badge } from "@/components/ui/Badge";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { OUTCOME_WORDS, formatCost, inboxOrder, outcomeBadgeTone, outcomeTone, placeOf } from "@/lib/world";

/**
 * Finished jobs nobody reviewed yet: a failure or an open pull request
 * first. One opens its report.
 */
export function QuestInbox({ quests, onOpen }: { quests: readonly InboxQuest[]; onOpen: (jobId: string) => void }) {
  const lookup = useIdentityLookup();
  if (quests.length === 0) {
    return (
      <p className="text-sm text-muted-foreground" data-testid="quest-inbox" data-count={0}>
        Nothing waiting for review.
      </p>
    );
  }
  return (
    <Card role="region" aria-labelledby="quest-inbox-title" data-testid="quest-inbox" data-count={quests.length}>
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
            return (
              <li key={q.job_id} className="w-64 shrink-0">
                <button
                  type="button"
                  className="flex w-full items-start gap-2.5 rounded-md border border-border bg-white/[0.02] p-2.5 text-left transition-colors hover:border-border-strong hover:bg-overlay"
                  onClick={() => onOpen(q.job_id)}
                  data-testid="inbox-quest"
                >
                  <AgentAvatar identity={identity} size={32} state={outcomeTone(q.outcome) === "loss" ? "failed" : "done"} />
                  <span className="min-w-0 flex-1 space-y-1">
                    <span className="block truncate text-sm text-fg">{q.title}</span>
                    <Badge tone={outcomeBadgeTone(q.outcome)}>{OUTCOME_WORDS[q.outcome]}</Badge>
                    <span className="block truncate text-2xs text-fg-muted">
                      {identity.displayName} · {placeOf(q.repo, q.area)} · {formatCost(q.cost_usd)}
                    </span>
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </CardContent>
    </Card>
  );
}
