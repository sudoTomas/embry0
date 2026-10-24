import type { AchievementEntry, Progression } from "@/api/progression";

export function entry(overrides: Partial<AchievementEntry> = {}): AchievementEntry {
  return {
    id: "forged_in_fire",
    name: "Forged in Fire",
    what_counts: "Pull requests merged",
    secret: false,
    state: "unlocked",
    tier: "steel",
    tiers_reached: 3,
    value: 16,
    next_threshold: 30,
    thresholds: [1, 5, 15, 30, 60, 120, 250],
    unlocked_at: "2026-10-01T00:00:00+00:00",
    ...overrides,
  };
}

export function progression(overrides: Partial<Progression> = {}): Progression {
  return {
    total_level: 937,
    total_xp: 31_000_000,
    stats: [
      { stat: "smithing", name: "Smithing", trained_by: "developer", xp: 2_100_000, level: 84, level_xp: 2_000_000, xp_to_next: 250_000 },
      { stat: "mining", name: "Mining", trained_by: "instance", xp: 13_500_000, level: 99, level_xp: 13_034_431, xp_to_next: null },
    ],
    agents: [],
    achievements: [
      entry(),
      entry({ id: "skill_cape", name: "Skill Cape", what_counts: "Skills at level 99", state: "discovered", tier: null, tiers_reached: 0, value: 0, next_threshold: 1, unlocked_at: null }),
      entry({ id: "oh_dear_you_are_dead", name: "???", what_counts: null, secret: true, state: "secret", tier: null, tiers_reached: 0, value: 0, next_threshold: null, thresholds: null, unlocked_at: null }),
    ],
    recent: [],
    computed_at: "2026-10-24T00:00:00+00:00",
    ...overrides,
  };
}
