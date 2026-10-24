import { api } from "./client";

/** A character's gear tier from its best skill (spec 2026-10-24): Bronze from 1 to Divine from 90. */
export type Tier = "bronze" | "iron" | "steel" | "mithril" | "adamant" | "rune" | "divine";

/** Where a skill's tile sits on a skill sheet. */
export type SkillGroup = "build" | "verify" | "investigate" | "care";

/** One of the 23 skills every character has. */
export interface Skill {
  /** Its id, such as `version_control`. */
  id: string;
  /** Its name, such as `Version control`. */
  name: string;
  group: SkillGroup;
  /** The classic skill it echoes, which gives it its icon and cape colours. */
  echo: string;
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

/** An agent type as a character: its 23 skills, their total, and the gear its best skill wears. */
export interface Character {
  agent_type: string;
  xp: number;
  /** Its 23 levels added up, 23 to 2,277. */
  total_level: number;
  best_skill: string;
  tier: Tier;
  /** Its skills at 99, the most experience first: the first is the skill cape it wears. */
  capes: string[];
  /** The rares its work earned (`party-hat`, `holiday-mask`, `white-party-hat`), first earned first. */
  rares: { id: string; at: string }[];
  /** Every quest done: the highest total level wears the quest cape. */
  quest_cape: boolean;
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
  /** Divine Slayer's reward: a Divine trim on every cape. */
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
export interface AchievementEntry {
  id: string;
  name: string;
  what_counts: string | null;
  secret: boolean;
  state: "unlocked" | "discovered" | "secret";
  /** The highest tier reached, or null before Bronze. */
  tier: Tier | null;
  tiers_reached: number;
  value: number;
  next_threshold: number | null;
  /** The value of each tier, Bronze to Divine. */
  thresholds: number[] | null;
  unlocked_at: string | null;
}

export interface AchievementUnlock {
  kind: "achievement";
  at: string;
  id: string;
  name: string;
  tier: Tier;
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

export type DiaryTierName = "easy" | "medium" | "hard" | "elite";

/** A repository's achievement diary, Easy to Elite. */
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

/** A rare a character's work earned. */
export interface RareDrop {
  kind: "rare";
  at: string;
  agent_type: string;
  id: string;
}

export type RecentEvent = LevelUp | RareDrop | AchievementUnlock | QuestComplete | DiaryComplete;

/** GET /progression: the account, its characters and its achievements, recomputed from the work embry0 recorded. */
export interface Progression {
  account: Account;
  skills: Skill[];
  characters: Character[];
  achievements: AchievementEntry[];
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
