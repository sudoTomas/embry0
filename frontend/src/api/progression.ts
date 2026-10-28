import { api } from "./client";

/** A tier's id in the active content pack (#176), such as `bronze`; its name and colour are in `Progression.tiers`. */
export type Tier = string;

/** A skill group's id in the active content pack, such as `build`. */
export type SkillGroup = string;

/** The two colours of a cloak, `#rrggbb`. */
export interface Cloak {
  base: string;
  trim: string;
}

/** One of the skills every character has, from the active content pack. */
export interface Skill {
  /** Its id, such as `version_control`. */
  id: string;
  /** Its name, such as `Version control`. */
  name: string;
  group: SkillGroup;
  /** The cloak a character wears with this skill at the top level. */
  cloak: Cloak;
}

/** A rung of the pack's tier ladder: gear from a level, and an achievement's step. */
export interface PackTier {
  id: string;
  name: string;
  from_level: number;
  color: string;
  /** The gear art step the dashboard draws for it (docs/contracts/progression.json). */
  gear: string;
}

/** A rare the pack can drop, in wear order; `art` is the head item it wears, or null for none. */
export interface PackRare {
  id: string;
  name: string;
  art: string | null;
}

/** A character's progress in one skill. */
export interface SkillProgress {
  stat: string;
  xp: number;
  level: number;
  /** The experience this level starts at. */
  level_xp: number;
  /** Experience left to the next level; null at 99. */
  xp_to_next: number | null;
}

/** An agent type as a character: its skills, their total, and the gear its best skill wears. */
export interface Character {
  agent_type: string;
  /** Its one level, from its five best skills (1 to 126 with the shipped pack). */
  level: number;
  xp: number;
  /** Its levels added up. */
  total_level: number;
  best_skill: string;
  tier: Tier;
  /** The gear art step its tier wears; absent from a backend older than the content pack. */
  gear?: string;
  /** Its skills at 99, the most experience first: the first is the mastery cloak it wears. */
  capes: string[];
  /** The rares its work earned, by id, first earned first. */
  rares: { id: string; at: string }[];
  /** Every quest done: the highest total level wears the quest cloak. */
  quest_cloak: boolean;
  /** Every skill, in sheet order. */
  skills: SkillProgress[];
}

/** The instance as the player's account. */
export interface Account {
  /** Every character's experience added up. */
  xp: number;
  /** Every character's total level added up. */
  total_level: number;
  characters: number;
  /** The points of every quest completed. */
  quest_points: number;
  /** A completed quest that trims every cloak in the Divine metal; absent from a backend older than the content pack. */
  cloak_trim?: boolean;
  /** The same, under its name before the content pack; kept for one release. */
  divine_trim: boolean;
}

export interface LevelUp {
  kind: "level";
  at: string;
  /** The character whose skill crossed the level. */
  agent_type: string;
  stat: string;
  level: number;
}

/** One achievement; a secret before its first signal carries no name, metric or thresholds. */
/** What a reached objective reads as, in place of a tier. */
export type AchievementTier = Tier | "complete";

/** A category of the achievement diary (spec 2026-10-25). */
export interface AchievementCategory {
  id: string;
  name: string;
  blurb: string;
}

/** What last moved an achievement: a pull request, a repository, an error. */
export interface AchievementEvidence {
  label: string | null;
  url: string | null;
  at: string | null;
}

export interface AchievementEntry {
  id: string;
  name: string;
  /** Its category's id; a secret shows its category too. */
  category: string;
  /** Tiered: one threshold per tier. Objective: one threshold, then complete. */
  kind: "tiered" | "objective";
  /** The pixel icon to draw; null while secret. */
  icon: string | null;
  what_counts: string | null;
  /** One line in the game's voice. */
  flavour: string | null;
  secret: boolean;
  state: "unlocked" | "discovered" | "secret";
  /** The highest tier reached ("complete" for an objective), or null before the first. */
  tier: AchievementTier | null;
  tiers_reached: number;
  value: number;
  next_threshold: number | null;
  /** The value of each tier, lowest first, or the one value of an objective. */
  thresholds: number[] | null;
  unlocked_at: string | null;
  /** When its highest tier was reached. */
  latest_at: string | null;
  evidence: AchievementEvidence | null;
}

export interface AchievementUnlock {
  kind: "achievement";
  at: string;
  id: string;
  name: string;
  tier: AchievementTier;
}

/** A step of a quest or a task of a repo diary, and when it was done. */
export interface Task {
  text: string;
  done_at: string | null;
}

export interface Quest {
  id: string;
  name: string;
  points: number;
  /** What completing it gives, in words. */
  reward: string;
  state: "not_started" | "in_progress" | "complete";
  requirement: string | null;
  requirement_met: boolean;
  /** The repository or issue its steps happened on, when they belong to one. */
  where: string | null;
  steps: Task[];
  completed_at: string | null;
}

/** A diary tier's id in the active content pack, such as `easy`; its name is in `Progression.diary_tiers`. */
export type DiaryTierName = string;

/** A repository's achievement diary, its tiers lowest first. */
export interface Diary {
  repo: string;
  tiers: { tier: DiaryTierName; tasks: Task[]; completed_at: string | null }[];
}

export interface QuestComplete {
  kind: "quest";
  at: string;
  id: string;
  name: string;
}

export interface DiaryComplete {
  kind: "diary";
  at: string;
  repo: string;
  tier: DiaryTierName;
}

/** A character's own level rising. */
export interface AgentLevelUp {
  kind: "agent_level";
  at: string;
  agent_type: string;
  level: number;
}

/** A rare a character's work earned. */
export interface RareDrop {
  kind: "rare";
  at: string;
  agent_type: string;
  id: string;
}

export type RecentEvent = LevelUp | AgentLevelUp | RareDrop | AchievementUnlock | QuestComplete | DiaryComplete;

/** The chat box's templates, one per kind of event, with `{name}` placeholders. */
export interface PackMessages {
  level: string;
  agent_level: string;
  rare: string;
  quest: string;
  diary: string;
  achievement_tier: string;
  achievement_complete: string;
}

/** The game's nouns the pages print; `gear`, `mastery_cloak` and `trimmed_cloak` take a placeholder. */
export interface PackLabels {
  complete: string;
  quest_journal: string;
  journal_points: string;
  quest_point: string;
  quest_points: string;
  gear: string;
  mastery_cloak: string;
  trimmed_cloak: string;
  quest_cloak: string;
}

/** GET /progression: the account, its characters and its achievements, recomputed from the work embry0 recorded,
 * and the active content pack's names, colours, words and art (#176). */
export interface Progression {
  account: Account;
  /** The active pack: its identity, schema version and calendar. */
  pack: { hash: string; version: number; timezone: string };
  groups: { id: string; name: string }[];
  skills: Skill[];
  /** The tier ladder, lowest first. */
  tiers: PackTier[];
  rares: PackRare[];
  cloaks: { quest: Cloak };
  diary_tiers: { id: string; name: string }[];
  messages: PackMessages;
  labels: PackLabels;
  curve: { max_level: number; character_max_level: number };
  characters: Character[];
  achievements: AchievementEntry[];
  achievement_categories: AchievementCategory[];
  quests: Quest[];
  /** The diaries with at least one task done, the furthest along first. */
  diaries: Diary[];
  /** Levels reached, rares dropped, tiers unlocked, quests and diary tiers completed, newest first. */
  recent: RecentEvent[];
  computed_at: string;
}

export async function fetchProgression(): Promise<Progression> {
  const { data } = await api.get<Progression>("/progression");
  return data;
}
