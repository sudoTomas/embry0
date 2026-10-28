import type { AchievementTier, Cloak, PackLabels, PackMessages, PackRare, PackTier, Progression, Skill } from "@/api/progression";

/**
 * The active content pack's names, colours and words (#176), as GET
 * /progression answers them, in one lookup. The dashboard keeps no copy of
 * them: every tier, rare, skill group, diary tier, chat-box message and
 * game noun shown is the pack's. While the answer is not there yet, or from
 * a backend older than the pack, a name falls back to its id written as a
 * word ("gold" reads "Gold"), a colour to the slate of an entry with no
 * tier, and the skill groups to the ones the skills name.
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

/** An id as a word: `quality_assurance` reads "Quality assurance", `paper-crown` "Paper crown". */
export function asWords(id: string): string {
  const words = id.replace(/[_-]+/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : id;
}

/** The skill groups the skills name, in the order they first appear: what an answer without `groups` still has. */
function groupsOf(skills: Skill[]): { id: string; name: string }[] {
  const ids = [...new Set(skills.map((s) => s.group))];
  return ids.map((id) => ({ id, name: asWords(id) }));
}

export function gamePackOf(p: PackFields): GamePack {
  const tiers = p.tiers ?? [];
  const rares = p.rares ?? [];
  return {
    tiers,
    tierById: new Map(tiers.map((t) => [t.id, t])),
    rares,
    rareById: new Map(rares.map((r) => [r.id, r])),
    groups: p.groups ?? groupsOf(p.skills ?? []),
    skillById: new Map((p.skills ?? []).map((s) => [s.id, s])),
    diaryTierName: new Map((p.diary_tiers ?? []).map((t) => [t.id, t.name])),
    messages: p.messages ?? null,
    labels: p.labels ?? null,
    questCloak: p.cloaks?.quest ?? null,
    maxLevel: p.curve?.max_level ?? 99,
  };
}

type Value = string | number | boolean;

/** A value as text, as the server writes it (embry0/services/progression_templates.py). */
function written(value: Value, grouped: boolean): string {
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number" && Number.isInteger(value)) return grouped ? value.toLocaleString("en-US") : String(value);
  return String(value);
}

const TOKEN = /\{\{|\}\}|\{([A-Za-z_][A-Za-z0-9_]*)(:,)?\}|[^{}]+/y;

/**
 * A template filled in the content pack's one grammar, the same as the
 * server's: `{name}` is a value, `{name:,}` a number with thousands
 * separators, `{{` and `}}` a literal brace. The server refuses any other
 * text when the pack is saved; one that somehow is not in the grammar is
 * shown as written, and a value that is missing keeps its field.
 * docs/contracts/progression.json carries the cases both sides are tested on.
 */
export function fill(template: string, values: Record<string, Value>): string {
  const out: string[] = [];
  TOKEN.lastIndex = 0;
  while (TOKEN.lastIndex < template.length) {
    const start = TOKEN.lastIndex;
    const match = TOKEN.exec(template);
    if (!match || match.index !== start) return template;
    const [token, name, grouped] = match;
    if (token === "{{") out.push("{");
    else if (token === "}}") out.push("}");
    else if (name === undefined) out.push(token);
    // Own keys only: `in` would also find `constructor` or `toString` on the object's prototype.
    else if (Object.hasOwn(values, name)) out.push(written(values[name], grouped !== undefined));
    else out.push(token);
  }
  return out.join("");
}

export function tierName(pack: GamePack | null, id: string): string {
  return pack?.tierById.get(id)?.name ?? asWords(id);
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
  return label(pack, "complete");
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
  return pack?.rareById.get(id)?.name ?? asWords(id).toLowerCase();
}

export function diaryTierName(pack: GamePack | null, id: string): string {
  return pack?.diaryTierName.get(id) ?? asWords(id);
}

/**
 * One of the pack's game nouns, its placeholders filled. Without the pack the
 * key is written as words after its values: `gear` with a tier reads "Gold gear".
 */
export function label(pack: GamePack | null, key: keyof PackLabels, values: Record<string, Value> = {}): string {
  const template = pack?.labels?.[key];
  if (template !== undefined) return fill(template, values);
  const given = Object.values(values).map((v) => written(v, false));
  return given.length ? `${given.join(" ")} ${asWords(key).toLowerCase()}` : asWords(key);
}

/** A colour lightened enough to read as text on the dark panels (obsidian and crimson are dark). */
export function readable(color: string): string {
  return `color-mix(in srgb, ${color} 62%, white)`;
}
