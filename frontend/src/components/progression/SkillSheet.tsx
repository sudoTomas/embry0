import type { Character, Skill, SkillGroup, SkillProgress } from "@/api/progression";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import type { AgentIdentity } from "@/lib/agentIdentity";
import { TIER_NAME } from "@/lib/tiers";
import { RARE_NAMES } from "./chatbox";

const number = (n: number) => n.toLocaleString("en-US");

const GROUPS: { id: SkillGroup; name: string }[] = [
  { id: "build", name: "Build" },
  { id: "verify", name: "Verify" },
  { id: "investigate", name: "Investigate" },
  { id: "care", name: "Care" },
];

/** One skill of a character: its level and a bar to the next level. */
function SkillTile({ skill, progress }: { skill: Skill; progress: SkillProgress }) {
  const span = progress.xp_to_next === null ? 0 : progress.xp - progress.level_xp + progress.xp_to_next;
  const filled = span === 0 ? 1 : (progress.xp - progress.level_xp) / span;
  return (
    <div className="rounded-md border border-border bg-white/[0.02] p-2.5" data-testid="skill-tile" data-skill={skill.id}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="truncate text-sm font-semibold text-white/90">{skill.name}</span>
        <span className="font-mono text-base font-semibold text-white/90" data-testid="skill-level">
          {progress.level}
        </span>
      </div>
      <div
        className="mt-1.5 h-1.5 overflow-hidden rounded-full bg-white/10"
        role="progressbar"
        aria-label={`${skill.name} progress to level ${Math.min(progress.level + 1, 99)}`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(filled * 100)}
      >
        <div className="h-full bg-white/50" style={{ width: `${Math.round(filled * 100)}%` }} />
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        {progress.xp_to_next === null
          ? `${number(progress.xp)} XP`
          : `${number(progress.xp_to_next)} XP to level ${progress.level + 1}`}
      </p>
    </div>
  );
}

/**
 * A character's sheet (progression spec 2026-10-24): it in what it wears,
 * its level and total level, what it earned, and its 23 skills in the
 * classic three-column grid, grouped Build, Verify, Investigate, Care.
 */
export function SkillSheet({ character, skills, identity }: { character: Character; skills: Skill[]; identity: AgentIdentity }) {
  const progressOf = new Map(character.skills.map((s) => [s.stat, s]));
  const name = identity.displayName;
  const skillName = new Map(skills.map((s) => [s.id, s.name]));
  // What it wears for what it earned: the best 99's cape (trimmed for two or more) and its rares.
  const earned = [
    ...character.capes.slice(0, 1).map((stat) => `${skillName.get(stat) ?? stat} cape${character.capes.length > 1 ? " (trimmed)" : ""}`),
    ...(character.quest_cape && character.capes.length === 0 ? ["quest cape"] : []),
    ...character.rares.map((r) => RARE_NAMES[r.id] ?? r.id),
  ];
  return (
    <section className="space-y-3" data-testid="skill-sheet" aria-label={`${name}'s skills`}>
      <div className="flex items-center gap-3">
        <AgentAvatar identity={identity} size={64} />
        <div>
          <h2 className="text-section font-semibold">
            {name}'s skills{" "}
            <span className="font-normal text-fg-secondary" data-testid="sheet-levels">
              · level {character.level} · total level {number(character.total_level)}
            </span>
          </h2>
          <p className="text-xs text-muted-foreground" data-testid="worn">
            {TIER_NAME[character.tier]} gear{earned.length ? ` · ${earned.join(", ")}` : ""}
          </p>
        </div>
      </div>
      {GROUPS.map((group) => (
        <div key={group.id} className="space-y-1.5">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-fg-secondary">{group.name}</h3>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {skills
              .filter((s) => s.group === group.id)
              .map((skill) => {
                const progress = progressOf.get(skill.id);
                return progress ? <SkillTile key={skill.id} skill={skill} progress={progress} /> : null;
              })}
          </div>
        </div>
      ))}
    </section>
  );
}

