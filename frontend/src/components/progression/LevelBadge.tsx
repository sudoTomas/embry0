import type { Tier } from "@/api/progression";
import { TIER_COLOR, TIER_NAME } from "@/lib/tiers";

/**
 * An agent's skill and level, with its tier as a small square of the tier's
 * metal; the title says the tier and what the next level needs.
 */
export function LevelBadge({
  skill,
  level,
  tier,
  xpToNext,
}: {
  skill: string;
  level: number;
  tier: Tier;
  xpToNext: number | null;
}) {
  const next = xpToNext === null ? "level 99" : `${xpToNext.toLocaleString("en-US")} XP to level ${level + 1}`;
  return (
    <span
      className="inline-flex items-center gap-1.5 whitespace-nowrap text-xs text-white/70"
      title={`${TIER_NAME[tier]} tier · ${next}`}
      data-testid="level-badge"
      data-tier={tier}
    >
      <span aria-hidden="true" className="h-2 w-2 rounded-[1px]" style={{ backgroundColor: TIER_COLOR[tier] }} />
      <span>
        {skill} <span className="font-mono font-semibold text-white/90">{level}</span>
      </span>
    </span>
  );
}
