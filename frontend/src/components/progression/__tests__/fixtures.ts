import type { AchievementEntry, Character, Progression, Skill, SkillProgress } from "@/api/progression";

/** A slice of the 23-skill catalogue: enough for each group to have a tile. */
export const SKILLS: Skill[] = [
  { id: "coding", name: "Coding", group: "build", echo: "smithing" },
  { id: "version_control", name: "Version control", group: "build", echo: "woodcutting" },
  { id: "testing", name: "Testing", group: "verify", echo: "cooking" },
  { id: "reading", name: "Reading", group: "investigate", echo: "fishing" },
  { id: "recovery", name: "Recovery", group: "care", echo: "hitpoints" },
];

export function skill(stat: string, overrides: Partial<SkillProgress> = {}): SkillProgress {
  return { stat, xp: 0, level: 1, level_xp: 0, xp_to_next: 83, ...overrides };
}

export function character(overrides: Partial<Character> = {}): Character {
  return {
    agent_type: "developer",
    xp: 15_134_431,
    total_level: 187,
    best_skill: "coding",
    tier: "divine",
    capes: ["coding"],
    rares: [],
    skills: [
      skill("coding", { xp: 13_500_000, level: 99, level_xp: 13_034_431, xp_to_next: null }),
      skill("version_control", { xp: 2_100_000, level: 84, level_xp: 2_000_000, xp_to_next: 250_000 }),
      skill("testing"),
      skill("reading"),
      skill("recovery"),
    ],
    ...overrides,
  };
}

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
    account: { xp: 15_134_431, total_level: 192, characters: 2 },
    skills: SKILLS,
    characters: [
      character({ agent_type: "review", xp: 0, total_level: 5, best_skill: "coding", tier: "bronze", capes: [], rares: [], skills: SKILLS.map((s) => skill(s.id)) }),
      character(),
    ],
    achievements: [
      entry(),
      entry({ id: "skill_cape", name: "Skill Cape", what_counts: "Skill capes earned across every character", state: "discovered", tier: null, tiers_reached: 0, value: 0, next_threshold: 1, unlocked_at: null }),
      entry({ id: "oh_dear_you_are_dead", name: "???", what_counts: null, secret: true, state: "secret", tier: null, tiers_reached: 0, value: 0, next_threshold: null, thresholds: null, unlocked_at: null }),
    ],
    recent: [],
    computed_at: "2026-10-24T00:00:00+00:00",
    ...overrides,
  };
}
