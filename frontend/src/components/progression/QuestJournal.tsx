import type { Quest } from "@/api/progression";
import { cn, formatDate } from "@/lib/utils";

/** The old quest list's colours: red not started, yellow in progress, green complete. */
const STATE: Record<Quest["state"], { label: string; className: string }> = {
  not_started: { label: "Not started", className: "text-[#e05a4f]" },
  in_progress: { label: "In progress", className: "text-[#e6c547]" },
  complete: { label: "Complete", className: "text-[#5fc95a]" },
};

/**
 * The account's quests (spec 2026-10-24) as a quest journal: each quest in
 * its state's colour, its steps struck through as they are done, what it
 * requires and what it rewards.
 */
export function QuestJournal({ quests, points }: { quests: Quest[]; points: number }) {
  const total = quests.reduce((n, q) => n + q.points, 0);
  return (
    <section className="space-y-2" aria-labelledby="quests-heading">
      <h2 id="quests-heading" className="text-section font-semibold">
        Quests <span className="font-normal text-fg-secondary">· {points} of {total} quest points</span>
      </h2>
      <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
        {quests.map((quest) => (
          <article key={quest.id} className="rounded-md border border-border bg-white/[0.02] p-3" data-testid="quest" data-state={quest.state}>
            <div className="flex items-baseline justify-between gap-2">
              <h3 className={cn("text-sm font-semibold", STATE[quest.state].className)}>{quest.name}</h3>
              <span className="shrink-0 text-xs text-muted-foreground">
                {STATE[quest.state].label} · {quest.points} {quest.points === 1 ? "point" : "points"}
              </span>
            </div>
            {quest.requirement && (
              <p className={cn("mt-1 text-xs", quest.requirement_met ? "text-white/40 line-through" : "text-white/70")} data-testid="quest-requirement">
                Requires: {quest.requirement}
              </p>
            )}
            <ol className="mt-2 space-y-0.5 text-xs">
              {quest.steps.map((step) => (
                <li key={step.text} className={step.done_at ? "text-white/40 line-through" : "text-white/80"} data-done={Boolean(step.done_at)}>
                  {step.text}
                </li>
              ))}
            </ol>
            <p className="mt-2 text-xs text-muted-foreground">
              Reward: {quest.reward}
              {quest.where && ` · on ${quest.where}`}
              {quest.completed_at && ` · completed ${formatDate(quest.completed_at)}`}
            </p>
          </article>
        ))}
      </div>
    </section>
  );
}
