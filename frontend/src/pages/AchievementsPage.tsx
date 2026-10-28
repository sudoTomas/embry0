import { useSearchParams } from "react-router";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { AchievementDiary } from "@/components/progression/AchievementDiary";
import { STATE_FILTERS, type StateFilter } from "@/components/progression/diaryFilters";
import { QuestJournal } from "@/components/progression/QuestJournal";
import { RepoDiaries } from "@/components/progression/RepoDiaries";
import { SkillSheet } from "@/components/progression/SkillSheet";
import { PageError } from "@/components/PageError";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { useProgression } from "@/hooks/useProgression";
import { TIER_COLOR } from "@/lib/tiers";
import { cn } from "@/lib/utils";

const number = (n: number) => n.toLocaleString("en-US");

/**
 * The achievement diary (spec 2026-10-25) and, below it, the account (spec
 * 2026-10-24): the player's experience and totals, its quests and repo
 * diaries, its characters as a roster, and the selected character's skill
 * sheet. `?character=<agent type>` picks the sheet; without it, the highest
 * level shows. `?category=` and `?state=` filter the diary.
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
  const category = params.get("category") ?? "all";
  const stateParam = params.get("state");
  const state: StateFilter = STATE_FILTERS.includes(stateParam as StateFilter) ? (stateParam as StateFilter) : "all";
  // Each filter is its own parameter, so picking one keeps the character and the other filter.
  const setParam = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  return (
    <div className="space-y-6" data-testid="achievements-page">
      <AchievementDiary
        data={data}
        category={category}
        state={state}
        onCategory={(id) => setParam("category", id === "all" ? null : id)}
        onState={(value) => setParam("state", value === "all" ? null : value)}
      />

      <header className="space-y-1 border-t border-border pt-6">
        <h2 className="title-page text-xl">The account</h2>
        <p className="text-sm text-fg-secondary" data-testid="account-summary">
          Account {number(data.account.xp)} XP · account total {number(data.account.total_level)} ·{" "}
          {number(data.account.characters)} characters · {unlocked} of {data.achievements.length} achievements unlocked ·{" "}
          {data.account.quest_points} quest points
        </p>
      </header>

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
                  onClick={() => setParam("character", character.agent_type)}
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
