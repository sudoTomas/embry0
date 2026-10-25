import { useMemo, useState } from "react";
import type { Lead } from "@/api/world";
import { KIND_WORDS, NO_FILTERS, filterLeads, levelBand, placeOf, type LeadFilters } from "@/lib/world";
import { CreatureIcon } from "./CreatureIcon";

interface QuestBoardProps {
  quests: readonly Lead[];
  /** The level a lead's colour is read against: the strongest character's. */
  partyLevel: number;
  onPick: (lead: Lead) => void;
}

/** The quest board: the work embry0 found, as creatures to attack and people with tasks, filterable by place, kind and level. */
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
    <section className="world-panel flex min-h-0 flex-col gap-2 p-3" aria-labelledby="quest-board-title" data-testid="quest-board">
      <div className="flex items-baseline justify-between gap-2">
        <h2 id="quest-board-title" className="world-title text-lg">
          Quest board
        </h2>
        <span className="world-muted text-xs">
          {shown.length} of {quests.length}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-1.5 text-xs">
        <label className="flex flex-col gap-0.5">
          <span className="world-muted">Region</span>
          <select
            aria-label="Region"
            className="world-slot h-7 px-1 world-text"
            value={filters.repo}
            onChange={(e) => set({ repo: e.target.value, area: "" })}
          >
            <option value="">Everywhere</option>
            {repos.map((r) => (
              <option key={r} value={r}>
                {placeOf(r)}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="world-muted">Area</span>
          <select aria-label="Area" className="world-slot h-7 px-1 world-text" value={filters.area} onChange={(e) => set({ area: e.target.value })} disabled={!areas.length}>
            <option value="">All areas</option>
            {areas.map((a) => (
              <option key={a} value={a}>
                {a}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="world-muted">Kind</span>
          <select aria-label="Kind" className="world-slot h-7 px-1 world-text" value={filters.kind} onChange={(e) => set({ kind: e.target.value })}>
            <option value="">Every kind</option>
            {kinds.map((k) => (
              <option key={k} value={k}>
                {KIND_WORDS[k] ?? k}
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-col gap-0.5">
          <span className="world-muted">Level up to</span>
          <select
            aria-label="Highest level"
            className="world-slot h-7 px-1 world-text"
            value={filters.maxLevel}
            onChange={(e) => set({ maxLevel: Number(e.target.value) })}
          >
            {[126, 90, 60, 40, 20].map((n) => (
              <option key={n} value={n}>
                {n === 126 ? "Any level" : `Level ${n}`}
              </option>
            ))}
          </select>
        </label>
      </div>
      <ul className="min-h-0 flex-1 space-y-1.5 overflow-y-auto pr-1" data-testid="quest-list">
        {shown.length === 0 && <li className="world-muted p-2 text-sm">No quests here right now.</li>}
        {shown.map((q) => (
          <li key={q.id}>
            <button
              type="button"
              className="world-slot flex w-full items-start gap-2 rounded-sm p-2 text-left"
              onClick={() => onPick(q)}
              data-testid="quest"
              data-kind={q.kind}
            >
              <CreatureIcon creature={q.creature} size={32} label={KIND_WORDS[q.kind] ?? q.kind} />
              <span className="min-w-0 flex-1">
                <span className="flex items-baseline justify-between gap-2">
                  <span className="world-text truncate text-sm">{q.title}</span>
                  <span className="world-level shrink-0 text-xs" data-band={levelBand(q.level, partyLevel)}>
                    lvl {q.level}
                  </span>
                </span>
                <span className="world-muted block truncate text-[11px]">
                  {placeOf(q.repo, q.area)} · {KIND_WORDS[q.kind] ?? q.kind}
                </span>
                <span className="world-subtitle mt-0.5 block text-[11px]">{q.verb}</span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
