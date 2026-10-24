import { useSearchParams } from "react-router";
import type { AchievementEntry, Character, Skill, SkillGroup, SkillProgress } from "@/api/progression";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { RARE_NAMES } from "@/components/progression/chatbox";
import { QuestJournal } from "@/components/progression/QuestJournal";
import { RepoDiaries } from "@/components/progression/RepoDiaries";
import { PageError } from "@/components/PageError";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import type { AgentIdentity } from "@/lib/agentIdentity";
import { useProgression } from "@/hooks/useProgression";
import { TIER_COLOR, TIER_NAME, TIER_ORDER } from "@/lib/tiers";
import { cn, formatDate } from "@/lib/utils";

const number = (n: number) => n.toLocaleString("en-US");

const GROUPS: { id: SkillGroup; name: string }[] = [
  { id: "build", name: "Build" },
  { id: "verify", name: "Verify" },
  { id: "investigate", name: "Investigate" },
  { id: "care", name: "Care" },
];

/** One skill of a character: its level and a bar to the next level. */
function SkillTile({ skill, progress }: { skill: Skill; progress: SkillProgress }) {
  const span = progress.xp_to_next === null ? 0 : progress.xp - progress.level_xp + progress.xp_to_next;
  const filled = span === 0 ? 1 : (progress.xp - progress.level_xp) / span;
  return (
    <div className="rounded-md border border-border bg-white/[0.02] p-2.5" data-testid="skill-tile" data-skill={skill.id}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="truncate text-sm font-semibold text-white/90">{skill.name}</span>
        <span className="font-mono text-base font-semibold text-white/90" data-testid="skill-level">
          {progress.level}
        </span>
      </div>
      <div
        className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-white/10"
        role="progressbar"
        aria-label={`${skill.name} progress to level ${Math.min(progress.level + 1, 99)}`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(filled * 100)}
      >
        <div className="h-full bg-white/50" style={{ width: `${Math.round(filled * 100)}%` }} />
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        {progress.xp_to_next === null
          ? `${number(progress.xp)} XP`
          : `${number(progress.xp_to_next)} XP to level ${progress.level + 1}`}
      </p>
    </div>
  );
}

/** A character's 23 skills in the classic three-column grid, grouped Build, Verify, Investigate, Care. */
function SkillSheet({ character, skills, identity }: { character: Character; skills: Skill[]; identity: AgentIdentity }) {
  const progressOf = new Map(character.skills.map((s) => [s.stat, s]));
  const name = identity.displayName;
  const skillName = new Map(skills.map((s) => [s.id, s.name]));
  // What it wears for what it earned: the best 99's cape (trimmed for two or more) and its rares.
  const earned = [
    ...character.capes.slice(0, 1).map((stat) => `${skillName.get(stat) ?? stat} cape${character.capes.length > 1 ? " (trimmed)" : ""}`),
    ...(character.quest_cape && character.capes.length === 0 ? ["quest cape"] : []),
    ...character.rares.map((r) => RARE_NAMES[r.id] ?? r.id),
  ];
  return (
    <section className="space-y-3" data-testid="skill-sheet" aria-label={`${name}'s skills`}>
      <div className="flex items-center gap-3">
        <AgentAvatar identity={identity} size={64} />
        <div>
          <h2 className="text-section font-semibold">
            {name}'s skills <span className="font-normal text-fg-secondary">· total level {number(character.total_level)}</span>
          </h2>
          <p className="text-xs text-muted-foreground" data-testid="worn">
            {TIER_NAME[character.tier]} gear{earned.length ? ` · ${earned.join(", ")}` : ""}
          </p>
        </div>
      </div>
      {GROUPS.map((group) => (
        <div key={group.id} className="space-y-1.5">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-fg-secondary">{group.name}</h3>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {skills
              .filter((s) => s.group === group.id)
              .map((skill) => {
                const progress = progressOf.get(skill.id);
                return progress ? <SkillTile key={skill.id} skill={skill} progress={progress} /> : null;
              })}
          </div>
        </div>
      ))}
    </section>
  );
}

/** One achievement: its seven tier pips, its progress to the next tier, and what counts. */
function AchievementCard({ entry }: { entry: AchievementEntry }) {
  const hidden = entry.state === "secret";
  return (
    <div
      className={cn("rounded-md border border-border p-3", entry.state === "unlocked" ? "bg-white/[0.04]" : "bg-white/[0.01]")}
      data-testid="achievement"
      data-state={entry.state}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className={cn("text-sm font-semibold", hidden ? "text-white/40" : "text-white/90")}>{entry.name}</p>
          <p className="text-xs text-muted-foreground">{hidden ? "A secret achievement." : entry.what_counts}</p>
        </div>
        <span className="flex shrink-0 gap-0.5" aria-label={entry.tier ? `${TIER_NAME[entry.tier]} tier` : "No tier yet"} role="img">
          {TIER_ORDER.map((tier, i) => (
            <span
              key={tier}
              data-testid="tier-pip"
              data-reached={i < entry.tiers_reached}
              className="h-2.5 w-2.5 rounded-[1px]"
              style={{ backgroundColor: i < entry.tiers_reached ? TIER_COLOR[tier] : "rgba(255,255,255,0.08)" }}
              title={TIER_NAME[tier]}
            />
          ))}
        </span>
      </div>
      {!hidden && (
        <p className="mt-2 text-xs text-white/60" data-testid="achievement-progress">
          {entry.next_threshold === null
            ? `${number(entry.value)} · every tier reached`
            : `${number(entry.value)} of ${number(entry.next_threshold)} for ${TIER_NAME[TIER_ORDER[entry.tiers_reached]]}`}
          {entry.unlocked_at && ` · first unlocked ${formatDate(entry.unlocked_at)}`}
        </p>
      )}
    </div>
  );
}

/**
 * The account (spec 2026-10-24): the player's experience and totals, the
 * achievement diary it earned across everything, its quests and repo
 * diaries, its characters as a roster, and the selected character's skill
 * sheet. `?character=<agent type>` picks
 * the sheet; without it, the highest total level shows.
 */
export function AchievementsPage() {
  const progression = useProgression();
  const lookup = useIdentityLookup();
  const [params, setParams] = useSearchParams();

  if (progression.isError) {
    return <PageError message="Progression did not load" onRetry={() => void progression.refetch()} />;
  }
  if (!progression.data) return <Skeleton className="h-64 w-full" />;
  const { data } = progression;
  const unlocked = data.achievements.filter((a) => a.state === "unlocked").length;
  const roster = [...data.characters].sort((a, b) => b.total_level - a.total_level || a.agent_type.localeCompare(b.agent_type));
  const skillName = new Map(data.skills.map((s) => [s.id, s.name]));
  const selected = roster.find((c) => c.agent_type === params.get("character")) ?? roster[0];

  return (
    <div className="space-y-6" data-testid="achievements-page">
      <header className="space-y-1">
        <h1 className="title-pixel text-xl font-bold">Achievements</h1>
        <p className="text-sm text-fg-secondary" data-testid="account-summary">
          Account {number(data.account.xp)} XP · account total {number(data.account.total_level)} ·{" "}
          {number(data.account.characters)} characters · {unlocked} of {data.achievements.length} achievements unlocked ·{" "}
          {data.account.quest_points} quest points
        </p>
      </header>

      <section className="space-y-2">
        <h2 className="text-section font-semibold">Achievement diary</h2>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {data.achievements.map((entry) => (
            <AchievementCard key={entry.id} entry={entry} />
          ))}
        </div>
      </section>

      <QuestJournal quests={data.quests} points={data.account.quest_points} />

      <RepoDiaries diaries={data.diaries} />

      <section className="space-y-2">
        <h2 className="text-section font-semibold">Characters</h2>
        <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {roster.map((character) => {
            const identity = lookup(character.agent_type);
            const best = character.skills.find((s) => s.stat === character.best_skill);
            const isSelected = character === selected;
            return (
              <li key={character.agent_type}>
                <button
                  type="button"
                  data-testid="character"
                  aria-current={isSelected ? "true" : undefined}
                  onClick={() => setParams({ character: character.agent_type }, { replace: true })}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-md border p-2.5 text-left",
                    isSelected ? "border-white/40 bg-white/[0.06]" : "border-border bg-white/[0.02] hover:bg-white/[0.04]",
                  )}
                >
                  <AgentAvatar identity={identity} size={32} />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm font-semibold text-white/90">{identity.displayName}</span>
                    <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                      <span
                        aria-hidden="true"
                        className="h-2 w-2 rounded-[1px]"
                        style={{ backgroundColor: TIER_COLOR[character.tier] }}
                      />
                      {skillName.get(character.best_skill) ?? character.best_skill} {best?.level ?? 1}
                    </span>
                  </span>
                  <span className="font-mono text-sm font-semibold text-white/90" title="Total level">
                    {number(character.total_level)}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      </section>

      {selected && <SkillSheet character={selected} skills={data.skills} identity={lookup(selected.agent_type)} />}
    </div>
  );
}
