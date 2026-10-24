import type { AchievementEntry, StatProgress } from "@/api/progression";
import { PageError } from "@/components/PageError";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { useProgression } from "@/hooks/useProgression";
import { TIER_COLOR, TIER_NAME, TIER_ORDER } from "@/lib/tiers";
import { cn, formatDate } from "@/lib/utils";

const number = (n: number) => n.toLocaleString("en-US");

/** A skill: its level, a bar to the next level, and who trains it. */
function SkillTile({ stat, trainer }: { stat: StatProgress; trainer: string }) {
  const span = stat.xp_to_next === null ? 0 : stat.xp - stat.level_xp + stat.xp_to_next;
  const filled = span === 0 ? 1 : (stat.xp - stat.level_xp) / span;
  return (
    <div className="rounded-md border border-border bg-white/[0.02] p-3" data-testid="skill-tile">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-semibold text-white/90">{stat.name}</span>
        <span className="font-mono text-lg font-semibold text-white/90" data-testid="skill-level">
          {stat.level}
        </span>
      </div>
      <div
        className="mt-2 h-1.5 overflow-hidden rounded-full bg-white/10"
        role="progressbar"
        aria-label={`${stat.name} progress to level ${Math.min(stat.level + 1, 99)}`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(filled * 100)}
      >
        <div className="h-full bg-white/50" style={{ width: `${Math.round(filled * 100)}%` }} />
      </div>
      <p className="mt-1.5 text-xs text-muted-foreground">
        {trainer} · {stat.xp_to_next === null ? `${number(stat.xp)} XP` : `${number(stat.xp_to_next)} XP to level ${stat.level + 1}`}
      </p>
    </div>
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
 * The instance's progression (spec 2026-10-24): its total level, each skill
 * with who trains it, and the achievement diary in seven tiers, Bronze to
 * Divine, recomputed from the work embry0 recorded.
 */
export function AchievementsPage() {
  const progression = useProgression();
  const lookup = useIdentityLookup();

  if (progression.isError) {
    return <PageError message="Progression did not load" onRetry={() => void progression.refetch()} />;
  }
  if (!progression.data) return <Skeleton className="h-64 w-full" />;
  const { data } = progression;
  const unlocked = data.achievements.filter((a) => a.state === "unlocked").length;

  return (
    <div className="space-y-6" data-testid="achievements-page">
      <header className="space-y-1">
        <h1 className="title-pixel text-xl font-bold">Achievements</h1>
        <p className="text-sm text-fg-secondary" data-testid="total-level">
          Total level {number(data.total_level)} · {number(data.total_xp)} XP · {unlocked} of {data.achievements.length} achievements
          unlocked
        </p>
      </header>

      <section className="space-y-2">
        <h2 className="text-section font-semibold">Skills</h2>
        <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {data.stats.map((stat) => (
            <SkillTile
              key={stat.stat}
              stat={stat}
              trainer={stat.trained_by === "instance" ? "The instance" : lookup(stat.trained_by).displayName}
            />
          ))}
        </div>
      </section>

      <section className="space-y-2">
        <h2 className="text-section font-semibold">Achievement diary</h2>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {data.achievements.map((entry) => (
            <AchievementCard key={entry.id} entry={entry} />
          ))}
        </div>
      </section>
    </div>
  );
}
