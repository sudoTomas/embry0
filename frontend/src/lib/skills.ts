/** Mirror of embry0/agents/skills.py; tests/fixtures/skills/effective-skills.json pins both sides. */
export type SkillEntry = string | { name: string };
export interface SkillsDelta { add?: SkillEntry[]; remove?: SkillEntry[] | "*" }
export interface NormalizedDelta { add: string[]; remove: string[] | "*" }

const DELTA_ERROR = "skills delta must be a list or an object with add and remove";

/** A delta entry's name: a string as is, or the non-empty `name` of an object (`_entry_name`). */
export function skillName(entry: unknown): string {
  if (typeof entry === "string") return entry;
  if (entry && typeof entry === "object" && !Array.isArray(entry)) {
    const name = (entry as { name?: unknown }).name;
    if (typeof name === "string" && name) return name;
  }
  throw new Error(DELTA_ERROR);
}

/** `normalize_delta`: null is the identity, a bare list replaces, an object adds and removes.
 * As in Python, a present-but-null `add` or `remove` is refused; only an absent key defaults. */
export function normalizeDelta(value: unknown): NormalizedDelta {
  if (value === null || value === undefined) return { add: [], remove: [] };
  if (Array.isArray(value)) return { add: value.map(skillName), remove: "*" };
  if (typeof value === "object") {
    const { add = [], remove = [] } = value as { add?: unknown; remove?: unknown };
    if (!Array.isArray(add) || !(remove === "*" || Array.isArray(remove))) throw new Error(DELTA_ERROR);
    return {
      add: add.map(skillName),
      remove: remove === "*" ? "*" : (remove as unknown[]).map(skillName),
    };
  }
  throw new Error(DELTA_ERROR);
}

/** `effective_skills`: each delta in order, remove then add; base order then add order, deduplicated. */
export function effectiveSkills(base: string[], ...deltas: unknown[]): string[] {
  let current = [...new Set(base)];
  for (const raw of deltas) {
    const d = normalizeDelta(raw);
    if (d.remove === "*") {
      current = [];
    } else {
      const drop = new Set(d.remove);
      current = current.filter((s) => !drop.has(s));
    }
    for (const name of d.add) if (!current.includes(name)) current.push(name);
  }
  return current;
}

/** A name without a colon is the target repository's own `.claude/skills` entry. */
export function isRepositorySkill(name: string): boolean { return !name.includes(":"); }

export function isEmptyDelta(d: NormalizedDelta): boolean {
  return d.add.length === 0 && d.remove !== "*" && d.remove.length === 0;
}
