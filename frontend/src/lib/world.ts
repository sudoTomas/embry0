import type { LeadKind } from "@/api/leadKinds";
import type {
  CharacterState,
  InboxQuest,
  Lead,
  Outcome,
  Party,
  QuestScroll,
  WorldAccountWait,
  WorldCharacter,
} from "@/api/world";
import type { SpriteState } from "@/components/roster/sprites";
import type { BadgeTone } from "@/components/ui/Badge";
import { providerLabel, statusTone } from "@/lib/status";
import type { LogEvent } from "@/lib/types";
import { formatDateTime } from "@/lib/utils";

/** What an agent is doing, as its card and panel say it (the chat box keeps the game's words). */
export const STATE_WORDS: Record<CharacterState, string> = {
  idle: "Idle",
  following: "Assisting",
  travelling: "Starting",
  working: "Working",
  waiting: "Needs your answer",
  returning: "Finished",
  defeated: "Failed",
  recalled: "Cancelled",
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
  pr_open: "PR open",
  closed: "PR closed",
  done: "Report delivered",
  failed: "Failed",
  recalled: "Cancelled",
  out: "Running",
};

/** The job status each place and ending reads as, so its badge takes that status's colour (lib/status). */
const STATE_STATUS: Record<CharacterState, string> = {
  idle: "pending",
  following: "running",
  travelling: "running",
  working: "running",
  waiting: "awaiting_input",
  returning: "completed",
  defeated: "failed",
  recalled: "cancelled",
};

const OUTCOME_STATUS: Record<Outcome, string> = {
  merged: "pr_merged",
  pr_open: "open",
  closed: "pr_closed",
  done: "completed",
  failed: "failed",
  recalled: "cancelled",
  out: "running",
};

export function stateTone(state: CharacterState): BadgeTone {
  return statusTone(STATE_STATUS[state]);
}

export function outcomeBadgeTone(outcome: Outcome): BadgeTone {
  return statusTone(OUTCOME_STATUS[outcome]);
}

/** Whether a quest's ending is a win, a loss or neither. */
export function outcomeTone(outcome: Outcome): "win" | "loss" | "neutral" {
  if (outcome === "merged" || outcome === "pr_open" || outcome === "done")
    return "win";
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
  return rate === null || rate === undefined
    ? "—"
    : `${Math.round(rate * 100)}%`;
}

/** Where a party is, in words: the repository without its owner, and the area. */
export function placeOf(
  repo: string | null | undefined,
  area?: string | null,
): string {
  const name = (repo ?? "").split("/").pop() || "the wilds";
  return area ? `${name} · ${area}` : name;
}

/** A character's three best skills, highest first. */
export function bestSkills(
  character: Pick<WorldCharacter, "skills">,
  count = 3,
): { stat: string; level: number }[] {
  return [...character.skills]
    .sort((a, b) => b.level - a.level || b.xp - a.xp)
    .slice(0, count);
}

/** A spent account a party waits for, in words, with the reset in the viewer's own time. */
export function accountWaitWords(wait: WorldAccountWait): string {
  const until = formatDateTime(wait.until);
  if (wait.reason === "local_busy")
    return `every slot of the local model is taken; it tries again at ${until}`;
  return `the ${providerLabel(wait.provider)} account is ${wait.reason.replace(/_/g, " ")} until ${until}`;
}

/** What a chat line knows about the party it is about, for a debug copy. */
export type LineParty = Pick<
  Party,
  | "job_id"
  | "title"
  | "leader"
  | "repo"
  | "area"
  | "state"
  | "failure"
  | "error_code"
  | "error_message"
>;

export interface NarrationLine {
  key: string;
  text: string;
  tone: "info" | "win" | "loss" | "alert";
  /** When the change was seen, UTC ISO. */
  at: string;
  /** The party the line is about; a level-up names only its character. */
  party?: LineParty;
  agent?: string;
}

/**
 * What changed between two reads of the parties, as chat lines, oldest
 * change first: a party setting out, starting work, waiting for an answer,
 * going quiet, coming back with loot, or falling.
 */
export function narrate(
  before: readonly Party[] | null,
  after: readonly Party[],
  nameOf: (agent: string) => string,
  now: Date = new Date(),
): NarrationLine[] {
  if (before === null) return [];
  const at = now.toISOString();
  const previous = new Map(before.map((p) => [p.job_id, p]));
  const lines: NarrationLine[] = [];
  for (const party of after) {
    const was = previous.get(party.job_id);
    const who = nameOf(party.leader);
    const where = placeOf(party.repo, party.area);
    const quest = `“${party.title}”`;
    const {
      job_id,
      title,
      leader,
      repo,
      area,
      state,
      failure,
      error_code,
      error_message,
    } = party;
    const about = {
      at,
      agent: leader,
      party: {
        job_id,
        title,
        leader,
        repo,
        area,
        state,
        failure,
        error_code,
        error_message,
      },
    };
    const say = (key: string, text: string, tone: NarrationLine["tone"]) =>
      lines.push({ key: `${party.job_id}:${key}`, text, tone, ...about });
    if (!was) {
      say("out", `${who} sets off for ${quest} in ${where}.`, "info");
      continue;
    }
    if (was.state !== party.state) {
      if (party.state === "working")
        say("work", `${who} is at work on ${quest}.`, "info");
      if (party.state === "waiting" && party.account_wait)
        say(
          "wait",
          `${who} waits on ${quest}: ${accountWaitWords(party.account_wait)}.`,
          "info",
        );
      else if (party.state === "waiting")
        say("wait", `${who} needs your answer on ${quest}.`, "alert");
      if (party.state === "returning")
        say(
          "back",
          party.pr_url
            ? `${who} is back from ${quest} with a pull request!`
            : `${who} is back from ${quest} with a report.`,
          "win",
        );
      if (party.state === "defeated")
        say(
          "lost",
          `Oh dear, ${who} was defeated on ${quest}${party.failure ? `: ${party.failure}` : "."}`,
          "loss",
        );
      if (party.state === "recalled")
        say("recall", `${who} was recalled from ${quest}.`, "info");
    }
    if (party.stuck && !was.stuck)
      say("stuck", `${who} has gone quiet on ${quest}.`, "alert");
  }
  return lines;
}

/**
 * A chat line as plain text to paste into a bug report: when, the line,
 * the quest, the agent, where, the job and its page; for a failure, the
 * job's error code and its exact message.
 */
export function debugText(line: NarrationLine): string {
  const p = line.party;
  const rows: [string, string | null | undefined][] = [
    ["time", line.at],
    ["line", line.text],
    ["quest", p?.title],
    ["agent", p?.leader ?? line.agent],
    ["repo", p?.repo],
    ["area", p?.area],
    ["job", p?.job_id],
  ];
  if (p?.state === "defeated")
    rows.push(["error_code", p.error_code], ["error", p.error_message]);
  if (p) rows.push(["url", `/jobs/${p.job_id}`]);
  return rows
    .filter((row): row is [string, string] => !!row[1])
    .map(([k, v]) => `${k}: ${v}`)
    .join("\n");
}

/** The experience each character gained between two reads: what floats up over its head. */
export function xpDrops(
  before: readonly WorldCharacter[] | null,
  after: readonly WorldCharacter[],
): Map<string, number> {
  const drops = new Map<string, number>();
  if (before === null) return drops;
  const previous = new Map(before.map((c) => [c.agent_type, c.xp]));
  for (const c of after) {
    const was = previous.get(c.agent_type);
    if (was !== undefined && c.xp > was) drops.set(c.agent_type, c.xp - was);
  }
  return drops;
}

/** The characters whose level rose between two reads, each with the level it reached. */
export function levelUps(
  before: readonly WorldCharacter[] | null,
  after: readonly WorldCharacter[],
): Map<string, number> {
  const ups = new Map<string, number>();
  if (before === null) return ups;
  const previous = new Map(before.map((c) => [c.agent_type, c.level]));
  for (const c of after) {
    const was = previous.get(c.agent_type);
    if (was !== undefined && c.level > was) ups.set(c.agent_type, c.level);
  }
  return ups;
}

export interface LeadFilters {
  repo: string;
  area: string;
  kind: string;
  minLevel: number;
  maxLevel: number;
}

export const NO_FILTERS: LeadFilters = {
  repo: "",
  area: "",
  kind: "",
  minLevel: 1,
  maxLevel: 126,
};

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

/** The lead-kind catalogue (GET /lead-kinds) by id; empty until it is read, or on an older backend that serves none. */
export function kindsById(kinds: readonly LeadKind[] | undefined): ReadonlyMap<string, LeadKind> {
  return new Map((kinds ?? []).map((k) => [k.id, k]));
}

/** One quest of a kind, as its badge says it; the kind's own id when the catalogue does not name it. */
export function kindLabel(kinds: ReadonlyMap<string, LeadKind>, kind: string): string {
  return kinds.get(kind)?.label ?? kind;
}

/** Quests of a kind, as the board's filter says them; the kind's own id when the catalogue does not name it. */
export function kindPlural(kinds: ReadonlyMap<string, LeadKind>, kind: string): string {
  return kinds.get(kind)?.plural ?? kind;
}

/** A kind's hover: what it is and what found it; undefined when the catalogue does not name it. */
export function kindHint(kinds: ReadonlyMap<string, LeadKind>, kind: string): string | undefined {
  const k = kinds.get(kind);
  if (!k) return undefined;
  return `${k.about.charAt(0).toUpperCase()}${k.about.slice(1)}. Found by ${k.found_by}.`;
}

/** The level's colour band, as a monster's level reads against yours: green below, yellow near, red above. */
export function levelBand(
  lead: number,
  character: number,
): "low" | "even" | "high" {
  if (lead < character - 10) return "low";
  if (lead > character + 10) return "high";
  return "even";
}

/** The badge colour of each band. */
export const BAND_TONE: Record<ReturnType<typeof levelBand>, BadgeTone> = {
  low: "success",
  even: "warning",
  high: "error",
};

/** The inbox's order: losses and open pull requests first (they want a decision), newest first within. */
export function inboxOrder(quests: readonly InboxQuest[]): InboxQuest[] {
  const weight = (q: InboxQuest) =>
    q.outcome === "failed" ? 0 : q.outcome === "pr_open" ? 1 : 2;
  return [...quests].sort(
    (a, b) =>
      weight(a) - weight(b) ||
      Date.parse(b.finished_at ?? "") - Date.parse(a.finished_at ?? ""),
  );
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

/** The one-click answers a card offers without opening the report; an open pull request needs a look first. */
export type Quick = "again" | "dismiss" | "good";

export function quickActs(q: Pick<InboxQuest, "outcome" | "pr_url">): Quick[] {
  if (q.outcome === "pr_open") return [];
  if (q.outcome === "failed" || q.outcome === "recalled")
    return ["again", "dismiss"];
  if (q.outcome === "done") return ["good", "again"];
  return ["good", "dismiss"];
}

/** The verdicts a quest offers, the first one the likely answer, by how it came back. */
export type ReviewAct =
  "merge" | "good" | "bad" | "again" | "dismiss" | "back" | "reject";

export function reviewActs(
  quest: Pick<QuestScroll, "outcome" | "pr_url">,
): ReviewAct[] {
  const openPr =
    !!quest.pr_url && quest.outcome !== "merged" && quest.outcome !== "closed";
  if (openPr) return ["merge", "back", "reject", "again"];
  if (quest.outcome === "failed" || quest.outcome === "recalled")
    return ["again", "dismiss"];
  if (quest.outcome === "done") return ["good", "again", "bad"];
  return ["good", "bad", "dismiss"];
}
