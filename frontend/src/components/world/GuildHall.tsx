import type { Party, WorldCharacter } from "@/api/world";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { TIER_COLOR, TIER_NAME } from "@/lib/tiers";
import { STATE_POSE, STATE_WORDS, bestSkills, formatPercent, placeOf } from "@/lib/world";
import { cn } from "@/lib/utils";
import type { Tier } from "@/api/progression";

interface CharacterCardProps {
  character: WorldCharacter;
  party: Party | undefined;
  skillName: (stat: string) => string;
  xpDrop: number | undefined;
  selected: boolean;
  onSelect: () => void;
}

/** One character at its station: what it wears, its level and best skills, where it is now, and how its quests went. */
export function CharacterCard({ character, party, skillName, xpDrop, selected, onSelect }: CharacterCardProps) {
  const lookup = useIdentityLookup();
  const identity = lookup(character.agent_type);
  const tier = character.tier as Tier;
  const where = party ? `${party.title} · ${placeOf(party.repo, party.area)}` : null;
  return (
    <button
      type="button"
      onClick={onSelect}
      data-testid="world-character"
      data-state={character.state}
      data-selected={selected}
      className="world-slot relative flex w-full items-start gap-3 rounded-sm p-2.5 text-left"
    >
      <span className="relative shrink-0">
        <AgentAvatar identity={identity} size={48} state={STATE_POSE[character.state]} />
        {character.state === "waiting" && <span className="world-bubble absolute -top-3 left-8 whitespace-nowrap">?</span>}
        {xpDrop ? (
          <span key={xpDrop} className="world-xp-drop" data-testid="xp-drop">
            +{xpDrop.toLocaleString("en-US")} xp
          </span>
        ) : null}
      </span>
      <span className="min-w-0 flex-1">
        <span className="flex items-baseline justify-between gap-2">
          <span className="world-title truncate text-sm">{identity.displayName}</span>
          <span className="world-subtitle shrink-0 text-xs" title={`Level ${character.level} · total ${character.total_level}`}>
            Lvl {character.level}
          </span>
        </span>
        <span className="flex items-center gap-1.5 text-[11px]">
          <span aria-hidden="true" className="h-2 w-2 shrink-0 rounded-[1px]" style={{ backgroundColor: TIER_COLOR[tier] }} />
          <span className="world-muted shrink-0">{TIER_NAME[tier] ?? character.tier} gear</span>
        </span>
        <span className="world-muted block truncate text-[11px]">
          {bestSkills(character)
            .map((s) => `${skillName(s.stat)} ${s.level}`)
            .join(" · ")}
        </span>
        <span className={cn("mt-1 block truncate text-xs", character.state === "idle" ? "world-muted" : "world-text")}>
          {STATE_WORDS[character.state]}
          {where ? `: ${where}` : ""}
          {character.quests_out > 1 ? ` (+${character.quests_out - 1} more)` : ""}
        </span>
        <span className="mt-1 flex flex-wrap gap-x-2 text-[11px] world-muted">
          <span>
            {character.stats.quests} quests · {formatPercent(character.stats.success_rate)} won
          </span>
          {character.stats.ratings.good + character.stats.ratings.needs_work + character.stats.ratings.bad > 0 && (
            <span>
              rated {character.stats.ratings.good} good · {character.stats.ratings.needs_work} so-so · {character.stats.ratings.bad} bad
            </span>
          )}
        </span>
        {party?.stuck && (
          <span className="mt-1 block text-[11px]" style={{ color: "var(--loss)" }} data-testid="stuck">
            Gone quiet for a while: check on it
          </span>
        )}
        {character.idle_too_long && character.state === "idle" && (
          <span className="mt-1 block text-[11px] world-muted" data-testid="idle-too-long">
            Has not been out in weeks
          </span>
        )}
      </span>
    </button>
  );
}

interface GuildHallProps {
  characters: readonly WorldCharacter[];
  parties: readonly Party[];
  skillName: (stat: string) => string;
  xpDrops: Map<string, number>;
  selected: string | null;
  onSelect: (agentType: string) => void;
}

const ORDER: Record<string, number> = { waiting: 0, working: 1, travelling: 2, returning: 3, defeated: 4, following: 5, recalled: 6, idle: 7 };

/** The hall: every character, the ones that need you first, then the ones out, then the idle by level. */
export function GuildHall({ characters, parties, skillName, xpDrops, selected, onSelect }: GuildHallProps) {
  const byJob = new Map(parties.map((p) => [p.job_id, p]));
  const sorted = [...characters].sort(
    (a, b) => (ORDER[a.state] ?? 9) - (ORDER[b.state] ?? 9) || b.level - a.level || a.agent_type.localeCompare(b.agent_type),
  );
  const out = characters.filter((c) => c.state !== "idle").length;
  return (
    <section className="world-panel space-y-2 p-3" aria-labelledby="guild-hall-title" data-testid="guild-hall">
      <div className="flex items-baseline justify-between gap-2">
        <h2 id="guild-hall-title" className="world-title text-lg">
          Guild hall
        </h2>
        <span className="world-muted text-xs">
          {out} out · {characters.length - out} in the hall
        </span>
      </div>
      <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
        {sorted.map((c) => (
          <li key={c.agent_type}>
            <CharacterCard
              character={c}
              party={c.job_id ? byJob.get(c.job_id) : undefined}
              skillName={skillName}
              xpDrop={xpDrops.get(c.agent_type)}
              selected={selected === c.agent_type}
              onSelect={() => onSelect(c.agent_type)}
            />
          </li>
        ))}
      </ul>
    </section>
  );
}
