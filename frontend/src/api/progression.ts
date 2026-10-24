import { api } from "./client";

/** An agent's gear tier from its level (spec 2026-10-24): Bronze from 1 to Divine from 90. */
export type Tier = "bronze" | "iron" | "steel" | "mithril" | "adamant" | "rune" | "divine";

export interface StatProgress {
  /** The skill's id, such as `smithing`. */
  stat: string;
  /** Its name, such as `Smithing`. */
  name: string;
  /** The agent type that trains it, or `instance`. */
  trained_by: string;
  xp: number;
  level: number;
  /** The experience this level starts at. */
  level_xp: number;
  /** Experience left to the next level; null at 99. */
  xp_to_next: number | null;
}

export interface AgentProgress {
  agent_type: string;
  stat: string;
  xp: number;
  level: number;
  level_xp: number;
  xp_to_next: number | null;
  tier: Tier;
}

export interface LevelUp {
  kind: "level";
  at: string;
  stat: string;
  level: number;
  /** The agent whose work crossed the level; null for an instance skill. */
  agent_type: string | null;
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

/** GET /progression: levels recomputed from the work embry0 recorded. */
export interface Progression {
  total_level: number;
  total_xp: number;
  stats: StatProgress[];
  agents: AgentProgress[];
  achievements: AchievementEntry[];
  /** Levels reached and tiers unlocked, newest first. */
  recent: RecentEvent[];
  computed_at: string;
}

export async function fetchProgression(): Promise<Progression> {
  const { data } = await api.get<Progression>("/progression");
  return data;
}
