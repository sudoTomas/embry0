import type { AchievementEntry, Character, Progression, Skill, SkillProgress } from "@/api/progression";
import { gamePackOf, type GamePack } from "@/lib/gamePack";
import contractText from "../../../../../docs/contracts/progression.json?raw";

/**
 * The shipped content pack as GET /progression answers it (#176): generated
 * by the backend into docs/contracts/progression.json, never copied by hand.
 */
export const PACK_VIEW: Pick<
  Progression,
  "pack" | "groups" | "skills" | "tiers" | "rares" | "cloaks" | "achievement_categories" | "diary_tiers" | "messages" | "labels" | "curve"
> = JSON.parse(contractText).default_view;

/** The shipped pack as the components read it. */
export const PACK: GamePack = gamePackOf(PACK_VIEW);

/** A slice of the shipped skills: enough for each group to have a tile. */
export const SKILLS: Skill[] = ["coding", "version_control", "testing", "reading", "recovery"].map(
  (id) => PACK_VIEW.skills.find((s) => s.id === id)!,
);

export function skill(stat: string, overrides: Partial<SkillProgress> = {}): SkillProgress {
  return { stat, xp: 0, level: 1, level_xp: 0, xp_to_next: 83, ...overrides };
}

export function character(overrides: Partial<Character> = {}): Character {
  return {
    agent_type: "developer",
    level: 59,
    xp: 15_134_431,
    total_level: 187,
    best_skill: "coding",
    tier: "divine",
    gear: "divine",
    capes: ["coding"],
    rares: [],
    quest_cloak: false,
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
    name: "Shipped It",
    category: "shipping",
    kind: "tiered",
    icon: "anvil",
    what_counts: "Pull requests embry0 opened that merged",
    flavour: "Somebody ring the bell.",
    secret: false,
    state: "unlocked",
    tier: "gold",
    tiers_reached: 3,
    value: 16,
    next_threshold: 30,
    thresholds: [1, 5, 15, 30, 60, 120, 250],
    unlocked_at: "2026-10-01T00:00:00+00:00",
    latest_at: "2026-10-05T00:00:00+00:00",
    evidence: { label: "o/app #42", url: "https://forge/o/app/pull/42", at: "2026-10-05T00:00:00+00:00" },
    ...overrides,
  };
}

export function progression(overrides: Partial<Progression> = {}): Progression {
  return {
    ...PACK_VIEW,
    account: { xp: 15_134_431, total_level: 192, characters: 2, quest_points: 2, cloak_trim: false, divine_trim: false },
    skills: SKILLS,
    characters: [
      character({ agent_type: "review", level: 1, xp: 0, total_level: 5, best_skill: "coding", tier: "bronze", gear: "bronze", capes: [], rares: [], skills: SKILLS.map((s) => skill(s.id)) }),
      character(),
    ],
    achievements: [
      entry(),
      entry({ id: "mastery_cloak", name: "Mastery Cloak", category: "skilling", icon: "cape", what_counts: "Skills at 99 across every character", flavour: "A cape.", state: "discovered", tier: null, tiers_reached: 0, value: 0, next_threshold: 1, unlocked_at: null, latest_at: null, evidence: null }),
      entry({ id: "long_grind", name: "Long Grind", category: "oddities", kind: "objective", icon: "hourglass", what_counts: "A pull request that merged from a job that ran for more than two hours", flavour: "This was supposed to be a quick fix.", secret: true, tier: "complete", tiers_reached: 1, value: 1, next_threshold: null, thresholds: [1], latest_at: "2026-10-03T00:00:00+00:00", evidence: { label: "o/app #9", url: null, at: "2026-10-03T00:00:00+00:00" } }),
      entry({ id: "back_on_your_feet", name: "???", category: "grit", icon: null, what_counts: null, flavour: null, secret: true, state: "secret", tier: null, tiers_reached: 0, value: 0, next_threshold: null, thresholds: null, unlocked_at: null, latest_at: null, evidence: null }),
    ],
    achievement_categories: [
      { id: "shipping", name: "Shipping", blurb: "Pull requests that merged." },
      { id: "grit", name: "Grit", blurb: "Comebacks." },
      { id: "skilling", name: "Skilling", blurb: "Levels." },
      { id: "oddities", name: "Oddities", blurb: "Secrets." },
    ],
    quests: [
      {
        id: "try_again", name: "Try, Try Again", points: 1, reward: "1 quest point; Recovery XP", state: "complete",
        requirement: null, requirement_met: true, where: "iss-1",
        steps: [{ text: "Watch a job on an issue fail", done_at: "2026-10-02T00:00:00+00:00" }, { text: "Merge a later job on that issue", done_at: "2026-10-03T00:00:00+00:00" }],
        completed_at: "2026-10-03T00:00:00+00:00",
      },
      {
        id: "divine_warden", name: "Divine Warden", points: 5, reward: "5 quest points; a Divine trim on every cape", state: "not_started",
        requirement: "A character at total level 1,000", requirement_met: false, where: null,
        steps: [{ text: "Merge 100 pull requests", done_at: null }, { text: "Complete a repo diary at Elite", done_at: null }],
        completed_at: null,
      },
    ],
    diaries: [
      {
        repo: "o/app",
        tiers: [
          { tier: "easy", tasks: [{ text: "Finish a job", done_at: "2026-10-01T00:00:00+00:00" }], completed_at: "2026-10-01T00:00:00+00:00" },
          { tier: "medium", tasks: [{ text: "Merge a pull request", done_at: null }], completed_at: null },
          { tier: "hard", tasks: [{ text: "Merge 10 pull requests", done_at: null }], completed_at: null },
          { tier: "elite", tasks: [{ text: "Merge 50 pull requests", done_at: null }], completed_at: null },
        ],
      },
    ],
    recent: [],
    computed_at: "2026-10-24T00:00:00+00:00",
    ...overrides,
  };
}
