import type { AchievementTier, Cloak, PackLabels, PackMessages, PackRare, PackTier, Progression, Skill } from "@/api/progression";

/**
 * The active content pack's names, colours and words (#176), as GET
 * /progression answers them, in one lookup. The dashboard keeps no copy of
 * them: every tier, rare, skill group, diary tier, chat-box message and
 * game noun shown is the pack's. While the answer is not there yet (or for
 * an id the pack does not know), a name falls back to its id and a colour
 * to the slate of an entry with no tier.
 */
export interface GamePack {
  tiers: PackTier[];
  tierById: Map<string, PackTier>;
  rares: PackRare[];
  rareById: Map<string, PackRare>;
  groups: { id: string; name: string }[];
  skillById: Map<string, Skill>;
  diaryTierName: Map<string, string>;
  messages: PackMessages | null;
  labels: PackLabels | null;
  questCloak: Cloak | null;
  maxLevel: number;
}

/** What an achievement with no tier yet draws in. */
export const SLATE = "#55525e";
/** What a complete objective draws in: parchment, outside the tier ladder. */
export const COMPLETE_COLOR = "#d8ccaa";

type PackFields = Partial<Pick<Progression, "tiers" | "rares" | "groups" | "skills" | "diary_tiers" | "messages" | "labels" | "cloaks" | "curve">>;

export function gamePackOf(p: PackFields): GamePack {
  const tiers = p.tiers ?? [];
  const rares = p.rares ?? [];
  return {
    tiers,
    tierById: new Map(tiers.map((t) => [t.id, t])),
    rares,
    rareById: new Map(rares.map((r) => [r.id, r])),
    groups: p.groups ?? [],
    skillById: new Map((p.skills ?? []).map((s) => [s.id, s])),
    diaryTierName: new Map((p.diary_tiers ?? []).map((t) => [t.id, t.name])),
    messages: p.messages ?? null,
    labels: p.labels ?? null,
    questCloak: p.cloaks?.quest ?? null,
    maxLevel: p.curve?.max_level ?? 99,
  };
}

/** A template with its `{name}` placeholders filled; `{name:,}` writes a number with thousands separators. */
export function fill(template: string, values: Record<string, string | number>): string {
  return template.replace(/\{(\w+)(:,)?\}/g, (whole, name: string, comma?: string) => {
    const value = values[name];
    if (value === undefined) return whole;
    return comma && typeof value === "number" ? value.toLocaleString("en-US") : String(value);
  });
}

export function tierName(pack: GamePack | null, id: string): string {
  return pack?.tierById.get(id)?.name ?? id;
}

export function tierColor(pack: GamePack | null, id: string): string {
  return pack?.tierById.get(id)?.color ?? SLATE;
}

/** The tier ids, lowest first, as an achievement's thresholds run. */
export function tierOrder(pack: GamePack | null): string[] {
  return pack?.tiers.map((t) => t.id) ?? [];
}

/** What a complete objective is called. */
export function completeName(pack: GamePack | null): string {
  return pack?.labels?.complete ?? "complete";
}

/** An achievement tier's colour; a tierless entry draws in slate. */
export function achievementColor(pack: GamePack | null, tier: AchievementTier | null): string {
  if (tier === null) return SLATE;
  return tier === "complete" ? COMPLETE_COLOR : tierColor(pack, tier);
}

/** An achievement tier's name in words. */
export function achievementTierName(pack: GamePack | null, tier: AchievementTier): string {
  return tier === "complete" ? completeName(pack) : tierName(pack, tier);
}

export function rareName(pack: GamePack | null, id: string): string {
  return pack?.rareById.get(id)?.name ?? id;
}

export function diaryTierName(pack: GamePack | null, id: string): string {
  return pack?.diaryTierName.get(id) ?? id;
}

/** One of the pack's game nouns, its placeholders filled; the key itself before the pack is there. */
export function label(pack: GamePack | null, key: keyof PackLabels, values: Record<string, string | number> = {}): string {
  const template = pack?.labels?.[key];
  return template === undefined ? key : fill(template, values);
}

/** A colour lightened enough to read as text on the dark panels (obsidian and crimson are dark). */
export function readable(color: string): string {
  return `color-mix(in srgb, ${color} 62%, white)`;
}
