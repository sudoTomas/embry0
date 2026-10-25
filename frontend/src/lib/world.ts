import type { CharacterState, InboxQuest, Lead, Outcome, Party, WorldCharacter } from "@/api/world";
import type { SpriteState } from "@/components/roster/sprites";
import type { LogEvent } from "@/lib/types";

/** How a character's place reads on its card. */
export const STATE_WORDS: Record<CharacterState, string> = {
  idle: "In the hall",
  following: "Out with a party",
  travelling: "Setting out",
  working: "On a quest",
  waiting: "Waiting for you",
  returning: "Back with loot",
  defeated: "Defeated",
  recalled: "Recalled",
};

/** The sprite pose for each place: out working swings its tool, waiting thinks, back cheers, defeated slumps. */
export const STATE_POSE: Record<CharacterState, SpriteState> = {
  idle: "idle",
  following: "tool",
  travelling: "pending",
  working: "tool",
  waiting: "thinking",
  returning: "done",
  defeated: "failed",
  recalled: "idle",
};

export const OUTCOME_WORDS: Record<Outcome, string> = {
  merged: "Merged",
  pr_open: "Pull request open",
  closed: "Pull request closed",
  done: "Report delivered",
  failed: "Failed",
  recalled: "Recalled",
  out: "Still out",
};

/** Whether a quest's ending is a win, a loss or neither. */
export function outcomeTone(outcome: Outcome): "win" | "loss" | "neutral" {
  if (outcome === "merged" || outcome === "pr_open" || outcome === "done") return "win";
  if (outcome === "failed" || outcome === "closed") return "loss";
  return "neutral";
}

export function formatSeconds(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function formatCost(usd: number | null | undefined): string {
  if (usd === null || usd === undefined) return "—";
  return usd < 0.01 ? "<$0.01" : `$${usd.toFixed(2)}`;
}

export function formatPercent(rate: number | null | undefined): string {
  return rate === null || rate === undefined ? "—" : `${Math.round(rate * 100)}%`;
}

/** Where a party is, in words: the repository without its owner, and the area. */
export function placeOf(repo: string | null | undefined, area?: string | null): string {
  const name = (repo ?? "").split("/").pop() || "the wilds";
  return area ? `${name} · ${area}` : name;
}

/** A character's three best skills, highest first. */
export function bestSkills(character: Pick<WorldCharacter, "skills">, count = 3): { stat: string; level: number }[] {
  return [...character.skills].sort((a, b) => b.level - a.level || b.xp - a.xp).slice(0, count);
}

export interface NarrationLine {
  key: string;
  text: string;
  tone: "info" | "win" | "loss" | "alert";
}

/**
 * What changed between two reads of the parties, as chat lines, oldest
 * change first: a party setting out, starting work, waiting for an answer,
 * going quiet, coming back with loot, or falling.
 */
export function narrate(before: readonly Party[] | null, after: readonly Party[], nameOf: (agent: string) => string): NarrationLine[] {
  if (before === null) return [];
  const previous = new Map(before.map((p) => [p.job_id, p]));
  const lines: NarrationLine[] = [];
  for (const party of after) {
    const was = previous.get(party.job_id);
    const who = nameOf(party.leader);
    const where = placeOf(party.repo, party.area);
    const quest = `“${party.title}”`;
    if (!was) {
      lines.push({ key: `${party.job_id}:out`, text: `${who} sets off for ${quest} in ${where}.`, tone: "info" });
      continue;
    }
    if (was.state !== party.state) {
      if (party.state === "working") lines.push({ key: `${party.job_id}:work`, text: `${who} is at work on ${quest}.`, tone: "info" });
      if (party.state === "waiting") lines.push({ key: `${party.job_id}:wait`, text: `${who} needs your answer on ${quest}.`, tone: "alert" });
      if (party.state === "returning")
        lines.push({
          key: `${party.job_id}:back`,
          text: party.pr_url ? `${who} is back from ${quest} with a pull request!` : `${who} is back from ${quest} with a report.`,
          tone: "win",
        });
      if (party.state === "defeated")
        lines.push({ key: `${party.job_id}:lost`, text: `Oh dear, ${who} was defeated on ${quest}${party.failure ? `: ${party.failure}` : "."}`, tone: "loss" });
      if (party.state === "recalled") lines.push({ key: `${party.job_id}:recall`, text: `${who} was recalled from ${quest}.`, tone: "info" });
    }
    if (party.stuck && !was.stuck) lines.push({ key: `${party.job_id}:stuck`, text: `${who} has gone quiet on ${quest}.`, tone: "alert" });
  }
  return lines;
}

/** The experience each character gained between two reads: what floats up over its head. */
export function xpDrops(before: readonly WorldCharacter[] | null, after: readonly WorldCharacter[]): Map<string, number> {
  const drops = new Map<string, number>();
  if (before === null) return drops;
  const previous = new Map(before.map((c) => [c.agent_type, c.xp]));
  for (const c of after) {
    const was = previous.get(c.agent_type);
    if (was !== undefined && c.xp > was) drops.set(c.agent_type, c.xp - was);
  }
  return drops;
}

export interface LeadFilters {
  repo: string;
  area: string;
  kind: string;
  minLevel: number;
  maxLevel: number;
}

export const NO_FILTERS: LeadFilters = { repo: "", area: "", kind: "", minLevel: 1, maxLevel: 126 };

export function filterLeads(leads: readonly Lead[], f: LeadFilters): Lead[] {
  return leads.filter(
    (l) =>
      (!f.repo || l.repo === f.repo) &&
      (!f.area || l.area === f.area) &&
      (!f.kind || l.kind === f.kind) &&
      l.level >= f.minLevel &&
      l.level <= f.maxLevel,
  );
}

/** What each kind of quest is called on the board's filter chips. */
export const KIND_WORDS: Record<string, string> = {
  case: "Production errors",
  issue: "Open issues",
  finding: "Outdated instructions",
  ticket: "Backlog tickets",
  ci_failure: "Broken builds",
  security_alert: "Security alerts",
  flaky_test: "Flaky tests",
  review_comment: "Unanswered reviews",
};

/** The level's colour band, as a monster's level reads against yours: green below, yellow near, red above. */
export function levelBand(lead: number, character: number): "low" | "even" | "high" {
  if (lead < character - 10) return "low";
  if (lead > character + 10) return "high";
  return "even";
}

/** The inbox's order: losses and open pull requests first (they want a decision), newest first within. */
export function inboxOrder(quests: readonly InboxQuest[]): InboxQuest[] {
  const weight = (q: InboxQuest) => (q.outcome === "failed" ? 0 : q.outcome === "pr_open" ? 1 : 2);
  return [...quests].sort((a, b) => weight(a) - weight(b) || Date.parse(b.finished_at ?? "") - Date.parse(a.finished_at ?? ""));
}

/** One event as a line of what the party is doing; null for an event that says nothing a watcher needs. */
export function describeEvent(event: LogEvent): string | null {
  const e = event as unknown as Record<string, unknown>;
  const text = (v: unknown, max = 140) => {
    const s = typeof v === "string" ? v.replace(/\s+/g, " ").trim() : "";
    return s.length > max ? `${s.slice(0, max)}…` : s;
  };
  switch (e.type) {
    case "agent_started":
      return `${text(e.agent) || "an agent"} sets to work`;
    case "agent_completed":
      return `${text(e.agent) || "an agent"} finished its part`;
    case "node_started":
      return `step: ${text(e.node)}`;
    case "tool_call":
      return `${text(e.tool_name, 40)} ${text(e.summary, 110)}`.trim();
    case "progress":
      return text(e.message);
    case "text":
      return text(e.text) || null;
    case "error":
      return `error: ${text(e.message) || text(e.error)}`;
    case "job_finished":
      return "the party is heading home";
    default:
      return null;
  }
}
