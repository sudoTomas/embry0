import type { MouseEvent } from "react";
import { Link } from "react-router";
import type { Tier } from "@/api/progression";
import { TIER_COLOR, TIER_NAME } from "@/lib/tiers";

/**
 * A character's total level, with its tier as a small square of the tier's
 * metal; the title says which skill set the tier. It links to the
 * character's skill sheet.
 */
export function LevelBadge({
  agentType,
  totalLevel,
  bestSkill,
  bestLevel,
  tier,
}: {
  agentType: string;
  totalLevel: number;
  bestSkill: string;
  bestLevel: number;
  tier: Tier;
}) {
  return (
    <Link
      to={`/achievements?character=${encodeURIComponent(agentType)}`}
      // The badge sits in a clickable row; opening the sheet must not also open the row.
      onClick={(e: MouseEvent) => e.stopPropagation()}
      className="inline-flex items-center gap-1.5 whitespace-nowrap text-xs text-white/70 hover:text-white"
      title={`${TIER_NAME[tier]} tier from ${bestSkill} ${bestLevel} · open the skill sheet`}
      data-testid="level-badge"
      data-tier={tier}
    >
      <span aria-hidden="true" className="h-2 w-2 rounded-[1px]" style={{ backgroundColor: TIER_COLOR[tier] }} />
      <span>
        Total level <span className="font-mono font-semibold text-white/90">{totalLevel.toLocaleString("en-US")}</span>
      </span>
    </Link>
  );
}
