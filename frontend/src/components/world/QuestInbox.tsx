import type { InboxQuest } from "@/api/world";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { OUTCOME_WORDS, formatCost, inboxOrder, outcomeTone, placeOf } from "@/lib/world";

/**
 * The quests that came back and wait for the player's judgement, like loot
 * not yet picked up: a failure or an open pull request first.
 */
export function QuestInbox({ quests, onOpen }: { quests: readonly InboxQuest[]; onOpen: (jobId: string) => void }) {
  const lookup = useIdentityLookup();
  if (quests.length === 0) {
    return (
      <section className="world-panel p-3" data-testid="quest-inbox" data-count={0}>
        <p className="world-muted text-sm">No quests waiting for your judgement. Send someone out.</p>
      </section>
    );
  }
  return (
    <section className="world-panel world-unread space-y-2 p-3" aria-labelledby="quest-inbox-title" data-testid="quest-inbox" data-count={quests.length}>
      <h2 id="quest-inbox-title" className="world-title text-base">
        {quests.length} quest{quests.length === 1 ? "" : "s"} back: judge the work
      </h2>
      <ul className="flex gap-2 overflow-x-auto pb-1">
        {inboxOrder(quests).map((q) => {
          const tone = outcomeTone(q.outcome);
          const identity = lookup(q.leader);
          return (
            <li key={q.job_id} className="w-60 shrink-0">
              <button type="button" className="world-slot flex w-full items-start gap-2 rounded-sm p-2 text-left" onClick={() => onOpen(q.job_id)} data-testid="inbox-quest">
                <AgentAvatar identity={identity} size={32} state={tone === "loss" ? "failed" : "done"} />
                <span className="min-w-0 flex-1">
                  <span className="world-text block truncate text-xs">{q.title}</span>
                  <span className="block text-[11px]" style={{ color: tone === "win" ? "var(--win)" : tone === "loss" ? "var(--loss)" : "#b9ab8c" }}>
                    {OUTCOME_WORDS[q.outcome]}
                  </span>
                  <span className="world-muted block truncate text-[10px]">
                    {identity.displayName} · {placeOf(q.repo, q.area)} · {formatCost(q.cost_usd)}
                  </span>
                </span>
              </button>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
