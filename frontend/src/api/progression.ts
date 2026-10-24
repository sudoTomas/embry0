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
  /** Experience left to the next level; null at 99. */
  xp_to_next: number | null;
}

export interface AgentProgress {
  agent_type: string;
  stat: string;
  xp: number;
  level: number;
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

/** GET /progression: levels recomputed from the work embry0 recorded. */
export interface Progression {
  total_level: number;
  total_xp: number;
  stats: StatProgress[];
  agents: AgentProgress[];
  recent: LevelUp[];
  computed_at: string;
}

export async function fetchProgression(): Promise<Progression> {
  const { data } = await api.get<Progression>("/progression");
  return data;
}
