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

export type RecentEvent = LevelUp | AchievementUnlock;

/** GET /progression: the account, its characters and its achievements, recomputed from the work embry0 recorded. */
export interface Progression {
  account: Account;
  skills: Skill[];
  characters: Character[];
  achievements: AchievementEntry[];
  /** Levels reached and tiers unlocked, newest first. */
  recent: RecentEvent[];
  computed_at: string;
}

export async function fetchProgression(): Promise<Progression> {
  const { data } = await api.get<Progression>("/progression");
  return data;
}
