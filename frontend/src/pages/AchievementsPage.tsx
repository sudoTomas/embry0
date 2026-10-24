import { useSearchParams } from "react-router";
import type { AchievementEntry } from "@/api/progression";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { QuestJournal } from "@/components/progression/QuestJournal";
import { RepoDiaries } from "@/components/progression/RepoDiaries";
import { SkillSheet } from "@/components/progression/SkillSheet";
import { PageError } from "@/components/PageError";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { useProgression } from "@/hooks/useProgression";
import { TIER_COLOR, TIER_NAME, TIER_ORDER } from "@/lib/tiers";
import { cn, formatDate } from "@/lib/utils";

const number = (n: number) => n.toLocaleString("en-US");

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
 * the sheet; without it, the highest level shows.
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
  const roster = [...data.characters].sort((a, b) => b.level - a.level || b.total_level - a.total_level || a.agent_type.localeCompare(b.agent_type));
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
                  <span className="font-mono text-sm font-semibold text-white/90" title={`Level · total level ${number(character.total_level)}`}>
                    {character.level}
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
