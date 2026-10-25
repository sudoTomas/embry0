import { useMemo, useState } from "react";
import type { Lead } from "@/api/world";
import { Badge } from "@/components/ui/Badge";
import { Card, CardContent, CardHeader } from "@/components/ui/Card";
import { FilterSelect } from "@/components/ui/FilterSelect";
import { BAND_TONE, KIND_WORD, KIND_WORDS, NO_FILTERS, filterLeads, levelBand, placeOf, type LeadFilters } from "@/lib/world";
import { CreatureIcon } from "./CreatureIcon";

interface QuestBoardProps {
  quests: readonly Lead[];
  /** The level a lead's colour is read against: the strongest agent's. */
  partyLevel: number;
  onPick: (lead: Lead) => void;
}

const LEVEL_CAPS = ["90", "60", "40", "20"];

/**
 * The work embry0 found (watcher problems, open issues, outdated
 * instructions, the finders' leads), hardest first, filterable by
 * repository, area, kind and level. Each row wears its creature and the
 * game's verb for it; a click opens it.
 */
export function QuestBoard({ quests, partyLevel, onPick }: QuestBoardProps) {
  const [filters, setFilters] = useState<LeadFilters>(NO_FILTERS);
  const repos = useMemo(() => [...new Set(quests.map((q) => q.repo))].sort(), [quests]);
  const areas = useMemo(
    () => [...new Set(quests.filter((q) => !filters.repo || q.repo === filters.repo).map((q) => q.area).filter((a): a is string => !!a))].sort(),
    [quests, filters.repo],
  );
  const kinds = useMemo(() => [...new Set(quests.map((q) => q.kind))], [quests]);
  const shown = filterLeads(quests, filters).sort((a, b) => b.level - a.level);
  const set = (patch: Partial<LeadFilters>) => setFilters((f) => ({ ...f, ...patch }));

  return (
    <Card role="region" className="flex min-h-0 flex-col" aria-labelledby="quest-board-title" data-testid="quest-board">
      <CardHeader className="flex-row items-baseline justify-between space-y-0 pb-3">
        <h2 id="quest-board-title" className="text-section font-semibold">
          Leads
        </h2>
        <span className="text-xs text-fg-muted">
          {shown.length} of {quests.length}
        </span>
      </CardHeader>
      <CardContent className="flex min-h-0 flex-1 flex-col gap-3">
        <div className="grid grid-cols-2 gap-2">
          <FilterSelect label="Repositories" value={filters.repo || undefined} options={repos} optionLabel={(r) => placeOf(r)} onChange={(v) => set({ repo: v ?? "", area: "" })} />
          <FilterSelect label="Areas" value={filters.area || undefined} options={areas} onChange={(v) => set({ area: v ?? "" })} />
          <FilterSelect label="Kinds" value={filters.kind || undefined} options={kinds} optionLabel={(k) => KIND_WORDS[k] ?? k} onChange={(v) => set({ kind: v ?? "" })} />
          <FilterSelect
            label="Levels"
            value={filters.maxLevel === NO_FILTERS.maxLevel ? undefined : String(filters.maxLevel)}
            options={LEVEL_CAPS}
            optionLabel={(n) => `Up to level ${n}`}
            onChange={(v) => set({ maxLevel: v ? Number(v) : NO_FILTERS.maxLevel })}
          />
        </div>
        <ul className="min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-1" data-testid="quest-list">
          {shown.length === 0 && <li className="py-2 text-sm text-muted-foreground">No leads match.</li>}
          {shown.map((q) => (
            <li key={q.id}>
              <button
                type="button"
                className="flex w-full items-start gap-3 rounded-md border border-border bg-white/[0.02] p-2.5 text-left transition-colors hover:border-border-strong hover:bg-overlay"
                onClick={() => onPick(q)}
                data-testid="quest"
                data-kind={q.kind}
              >
                <CreatureIcon kind={q.kind} creature={q.creature} size={32} label={KIND_WORDS[q.kind] ?? q.kind} />
                <span className="min-w-0 flex-1 space-y-1">
                  <span className="flex items-start justify-between gap-2">
                    <span className="line-clamp-2 text-sm text-fg">{q.title}</span>
                    <Badge tone={BAND_TONE[levelBand(q.level, partyLevel)]} className="hud shrink-0">
                      Lvl {q.level}
                    </Badge>
                  </span>
                  <span className="flex min-w-0 items-center gap-1.5">
                    <Badge tone="neutral">{KIND_WORD[q.kind] ?? q.kind}</Badge>
                    <span className="truncate font-mono text-2xs text-fg-muted">{placeOf(q.repo, q.area)}</span>
                    <span className="ml-auto shrink-0 text-2xs text-fg-muted" data-testid="quest-verb">
                      {q.verb}
                    </span>
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}
