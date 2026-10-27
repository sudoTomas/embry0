import type { Loot, Party, WorldCharacter } from "@/api/world";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { Badge } from "@/components/ui/Badge";
import { Card } from "@/components/ui/Card";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { TIER_COLOR, TIER_NAME } from "@/lib/tiers";
import { STATE_POSE, STATE_WORDS, bestSkills, formatPercent, placeOf, stateTone } from "@/lib/world";
import { cn } from "@/lib/utils";
import type { Tier } from "@/api/progression";
import type { ArtManifest } from "./art/manifest";
import { HallScene } from "./HallScene";

interface CharacterCardProps {
  character: WorldCharacter;
  party: Party | undefined;
  skillName: (stat: string) => string;
  xpDrop: number | undefined;
  selected: boolean;
  onSelect: () => void;
}

/** One agent: its sprite, level and best skills, what it is doing now, and its record. */
export function CharacterCard({ character, party, skillName, xpDrop, selected, onSelect }: CharacterCardProps) {
  const lookup = useIdentityLookup();
  const identity = lookup(character.agent_type);
  const tier = character.tier as Tier;
  const ratings = character.stats.ratings;
  const rated = ratings.good + ratings.needs_work + ratings.bad;
  return (
    <button
      type="button"
      onClick={onSelect}
      data-testid="world-character"
      data-state={character.state}
      data-selected={selected}
      className={cn(
        "relative flex w-full items-start gap-3 rounded-lg border border-border bg-card p-3 text-left transition-colors hover:border-border-strong hover:bg-overlay/60",
        selected && "border-border-strong bg-overlay",
      )}
    >
      <span className="relative shrink-0">
        <AgentAvatar identity={identity} size={48} state={STATE_POSE[character.state]} />
        {character.state === "waiting" && <span className="world-bubble absolute -top-3 left-8">?</span>}
        {xpDrop ? (
          <span key={xpDrop} className="world-xp-drop hud text-success" data-testid="xp-drop">
            +{xpDrop.toLocaleString("en-US")} xp
          </span>
        ) : null}
      </span>
      <span className="min-w-0 flex-1 space-y-1">
        <span className="flex items-baseline justify-between gap-2">
          <span className="truncate text-sm font-semibold text-fg">{identity.displayName}</span>
          <span className="hud shrink-0 text-xs text-fg-secondary" title={`Level ${character.level} · total ${character.total_level}`}>
            Lvl {character.level}
          </span>
        </span>
        <span className="flex items-center gap-1.5 text-2xs text-fg-muted">
          <span aria-hidden="true" className="h-2 w-2 shrink-0 rounded-[2px]" style={{ backgroundColor: TIER_COLOR[tier] }} />
          <span className="shrink-0">{TIER_NAME[tier] ?? character.tier}</span>
          <span className="truncate">
            ·{" "}
            {bestSkills(character)
              .map((s) => `${skillName(s.stat)} ${s.level}`)
              .join(" · ")}
          </span>
        </span>
        <span className="flex min-w-0 items-center gap-1.5">
          <Badge tone={stateTone(character.state)}>{STATE_WORDS[character.state]}</Badge>
          {party && <span className="truncate text-xs text-fg-secondary">{`${party.title} · ${placeOf(party.repo, party.area)}`}</span>}
          {character.quests_out > 1 && <span className="shrink-0 text-2xs text-fg-muted">+{character.quests_out - 1} more</span>}
        </span>
        <span className="block text-2xs text-fg-muted">
          {character.stats.quests} jobs · {formatPercent(character.stats.success_rate)} succeeded
          {rated > 0 && ` · rated ${ratings.good} good, ${ratings.needs_work} needs work, ${ratings.bad} bad`}
        </span>
        {party?.stuck && (
          <span className="block" data-testid="stuck">
            <Badge tone="warning">No activity for 20 minutes</Badge>
          </span>
        )}
        {character.idle_too_long && character.state === "idle" && (
          <span className="block text-2xs text-fg-muted" data-testid="idle-too-long">
            Idle for two weeks or more
          </span>
        )}
      </span>
    </button>
  );
}

interface GuildHallProps {
  art: ArtManifest;
  characters: readonly WorldCharacter[];
  parties: readonly Party[];
  loot: readonly Loot[];
  skillName: (stat: string) => string;
  xpDrops: Map<string, number>;
  levelUps: ReadonlySet<string>;
  selected: string | null;
  onSelect: (agentType: string) => void;
  onBoard: () => void;
  onLoot: (jobId: string) => void;
}

const ORDER: Record<string, number> = { waiting: 0, working: 1, travelling: 2, returning: 3, defeated: 4, following: 5, recalled: 6, idle: 7 };

/**
 * The agents: the hall's inside drawn as a picture in a plain card, every
 * agent at its station; then every agent's card, the ones that need you
 * first, then the busy, then the idle by level. With no art the cards stand
 * alone.
 */
export function GuildHall({ art, characters, parties, loot, skillName, xpDrops, levelUps, selected, onSelect, onBoard, onLoot }: GuildHallProps) {
  const byJob = new Map(parties.map((p) => [p.job_id, p]));
  const sorted = [...characters].sort(
    (a, b) => (ORDER[a.state] ?? 9) - (ORDER[b.state] ?? 9) || b.level - a.level || a.agent_type.localeCompare(b.agent_type),
  );
  const out = characters.filter((c) => c.state !== "idle").length;
  return (
    <section className="space-y-3" aria-labelledby="guild-hall-title" data-testid="guild-hall">
      <div className="flex items-baseline justify-between gap-2">
        <h2 id="guild-hall-title" className="text-section font-semibold">
          Agents
        </h2>
        <span className="text-xs text-fg-muted">
          {out} busy · {characters.length - out} idle
        </span>
      </div>
      {Object.keys(art.tilesets).length > 0 && (
        <Card className="night-frame overflow-hidden p-1.5">
          <HallScene
            art={art}
            characters={characters}
            loot={loot}
            xpDrops={xpDrops}
            levelUps={levelUps}
            onSelect={onSelect}
            onBoard={onBoard}
            onLoot={onLoot}
          />
        </Card>
      )}
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
