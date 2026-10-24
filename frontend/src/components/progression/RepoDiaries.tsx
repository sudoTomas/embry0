import { useState } from "react";
import type { Diary } from "@/api/progression";
import { cn } from "@/lib/utils";
import { DIARY_TIER_NAME } from "./chatbox";

/** How many diaries show before "Show all". */
const DIARIES_SHOWN = 8;

/**
 * An achievement diary per repository (spec 2026-10-24), Easy to Elite: one
 * row per repo with each tier's tasks done, a tier lit when all its tasks
 * are, and the row opening onto the tasks themselves.
 */
export function RepoDiaries({ diaries }: { diaries: Diary[] }) {
  const [open, setOpen] = useState<string | null>(null);
  const [all, setAll] = useState(false);
  const shown = all ? diaries : diaries.slice(0, DIARIES_SHOWN);
  return (
    <section className="space-y-2" aria-labelledby="diaries-heading">
      <h2 id="diaries-heading" className="text-section font-semibold">
        Repo diaries <span className="font-normal text-fg-secondary">· {diaries.length} started</span>
      </h2>
      {diaries.length === 0 ? (
        <p className="text-sm text-muted-foreground">No repository has done a diary task yet.</p>
      ) : (
        <ul className="divide-y divide-border rounded-md border border-border">
          {shown.map((diary) => {
            const isOpen = open === diary.repo;
            return (
              <li key={diary.repo} data-testid="diary">
                <button
                  type="button"
                  aria-expanded={isOpen}
                  onClick={() => setOpen(isOpen ? null : diary.repo)}
                  className="flex w-full flex-wrap items-center justify-between gap-2 px-3 py-2 text-left hover:bg-white/[0.03]"
                >
                  <span className="min-w-0 truncate font-mono text-xs text-white/90">{diary.repo}</span>
                  <span className="flex gap-1.5">
                    {diary.tiers.map((tier) => {
                      const done = tier.tasks.filter((t) => t.done_at).length;
                      return (
                        <span
                          key={tier.tier}
                          data-testid="diary-tier"
                          data-complete={Boolean(tier.completed_at)}
                          className={cn(
                            "rounded-sm px-1.5 py-0.5 text-[11px]",
                            tier.completed_at ? "bg-[#5fc95a]/20 text-[#7fdc7a]" : "bg-white/[0.05] text-white/50",
                          )}
                        >
                          {DIARY_TIER_NAME[tier.tier]} {done}/{tier.tasks.length}
                        </span>
                      );
                    })}
                  </span>
                </button>
                {isOpen && (
                  <div className="grid grid-cols-1 gap-3 px-3 pb-3 sm:grid-cols-2 lg:grid-cols-4" data-testid="diary-tasks">
                    {diary.tiers.map((tier) => (
                      <div key={tier.tier}>
                        <h3 className="text-xs font-semibold uppercase tracking-wide text-fg-secondary">{DIARY_TIER_NAME[tier.tier]}</h3>
                        <ul className="mt-1 space-y-0.5 text-xs">
                          {tier.tasks.map((task) => (
                            <li key={task.text} className={task.done_at ? "text-white/40 line-through" : "text-white/80"}>
                              {task.text}
                            </li>
                          ))}
                        </ul>
                      </div>
                    ))}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {!all && diaries.length > DIARIES_SHOWN && (
        <button type="button" className="text-xs text-white/60 underline hover:text-white" onClick={() => setAll(true)}>
          Show all {diaries.length}
        </button>
      )}
    </section>
  );
}
