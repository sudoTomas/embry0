import { api } from "./client";
import type { Evaluation, Rating, Verdict } from "./jobReview";
import { type RunWith, runWithBody } from "./runWith";
import type { RecentEvent } from "./progression";
import type { SlopMeterReading } from "./slopMeter";

/** The world (spec 2026-10-25): the guild hall, the quest board and the quests' scrolls. */

/** Where a character, or a party, is. */
export type PartyState = "travelling" | "working" | "waiting" | "returning" | "defeated" | "recalled";
export type CharacterState = PartyState | "idle" | "following";
export type Outcome = "merged" | "pr_open" | "closed" | "done" | "failed" | "recalled" | "out";

export interface CharacterStats {
  quests: number;
  succeeded: number;
  failed: number;
  merged: number;
  success_rate: number | null;
  avg_cost_usd: number | null;
  avg_seconds: number | null;
  last_quest_at: string | null;
  ratings: Record<Rating, number>;
}

export interface WorldCharacter {
  agent_type: string;
  level: number;
  total_level: number;
  xp: number;
  tier: string;
  best_skill: string;
  capes: string[];
  skills: { stat: string; level: number; xp: number }[];
  state: CharacterState;
  job_id: string | null;
  quests_out: number;
  stats: CharacterStats;
  idle_too_long: boolean;
  /** The template a quest the player writes for it runs; null for the developer's usual pipeline. */
  quest_template: string | null;
  can_quest: boolean;
}

export interface Party {
  job_id: string;
  repo: string | null;
  area: string | null;
  title: string;
  status: string;
  state: PartyState;
  leader: string;
  party: string[];
  working: string | null;
  lead_id: string | null;
  template: string | null;
  since: string | null;
  finished_at: string | null;
  pr_url: string | null;
  delivery_state: string | null;
  stuck: boolean;
  failure: string | null;
  /** A defeated party's job error code and its message as stored, for a debug copy; null otherwise. */
  error_code: string | null;
  error_message: string | null;
  /** A waiting party held by a spent LLM account, and until when (UTC ISO). Absent on an older backend. */
  account_wait?: WorldAccountWait | null;
}

/** What a paused quest waits for: a spent LLM account (spent account waits). */
export interface WorldAccountWait {
  provider: string;
  reason: string;
  until: string;
  model?: string | null;
  agent?: string | null;
  chosen?: boolean;
}

export interface Lead {
  id: string;
  kind: string;
  repo: string;
  area: string | null;
  owners: string[];
  title: string;
  summary: string;
  details: string;
  action: string;
  url: string | null;
  created_at: string;
  level: number;
  verb: string;
  creature: string;
  leader: string;
  severity?: string;
  occurrences?: number;
  path?: string;
  /** The slopometer's verdict on a watcher finding: a "slop" lead is cleaned, not fought. Absent when unclassified. */
  nature?: WorkNature["nature"];
  slop_score?: number;
  slop_kinds?: string[];
}

/**
 * One kind a quest on offer can be, as the backend's lead-kind catalogue
 * (embry0/services/lead_kinds.py), the one place its names live, serves it.
 */
export interface LeadKind {
  id: string;
  /** One of it, as a badge says it. */
  label: string;
  /** Several of it, as a filter says them. */
  plural: string;
  /** What it is, in a phrase. */
  about: string;
  /** What brings it in. */
  found_by: string;
}

/** The slopometer's verdict (spec 2026-10-25, slop dungeon): a bug, slop or noise, and why. */
export interface WorkNature {
  nature: "bug" | "slop" | "noise";
  slop_score: number;
  kinds: string[];
  evidence: { path: string; why: string }[];
  /** A Slopmaster cleanup's diff against its base, once it ran. */
  cleanup?: { added: number; removed: number };
}

/** A Slop Dungeon: a repository's (or area's) open slop, its score (0-100, falls as it is cleaned), and recent cleanups. */
export interface Dungeon {
  repo: string;
  area: string | null;
  score: number;
  open: number;
  cleaned: number;
  /** Its Slop-O-Meter reading (spec 2026-10-26); null when not scored, absent on an older backend. */
  meter?: SlopMeterReading | null;
}

export interface InboxQuest {
  job_id: string;
  repo: string | null;
  area: string | null;
  title: string;
  outcome: Outcome;
  leader: string;
  pr_url: string | null;
  finished_at: string | null;
  cost_usd: number;
  failure: string | null;
  error_code: string | null;
}

export interface Loot {
  job_id: string;
  repo: string | null;
  area: string | null;
  title: string;
  pr_url: string;
  merged: boolean;
  at: string | null;
}

export interface Region {
  repo: string;
  jobs: number;
  last_job_at: string | null;
  owned: boolean;
  watched: boolean;
  areas: { name: string; slug: string }[];
}

export interface World {
  generated_at: string;
  account: { xp: number; total_level: number; characters: number; quest_points: number };
  skills: { id: string; name: string }[];
  characters: WorldCharacter[];
  parties: Party[];
  quests: Lead[];
  /** Every kind a quest can be, with its names. Absent on an older backend. */
  lead_kinds?: LeadKind[];
  inbox: InboxQuest[];
  loot: Loot[];
  recent: RecentEvent[];
  regions: Region[];
  frontier: string[];
  /** Absent on an older backend. */
  dungeons?: Dungeon[];
}

export interface QuestLogEntry {
  job_id: string;
  repo: string | null;
  area: string | null;
  title: string;
  outcome: Outcome;
  pr_url: string | null;
  template: string | null;
  started_at: string | null;
  finished_at: string | null;
  cost_usd: number;
  seconds: number;
  rating: Rating | null;
  verdict: Verdict | null;
}

export interface CharacterDetail extends WorldCharacter {
  quest_log: QuestLogEntry[];
}

export interface QuestFallback {
  agent_type: string;
  model: string;
  instead_of: string;
  provider: string | null;
  reason: string | null;
  until: string | null;
  runs: number;
}

export interface QuestScroll {
  job_id: string;
  repo: string | null;
  area: string | null;
  title: string;
  task: string;
  status: string;
  outcome: Outcome;
  template: string | null;
  lead_id: string | null;
  report: string;
  report_truncated: boolean;
  failure: string | null;
  error_code: string | null;
  error_message: string | null;
  pr_url: string | null;
  pr: { number: number; state: string; files: number | null; additions: number | null; deletions: number | null; commits: number | null } | null;
  merge_approval: { id: string; status: string; pr_url: string } | null;
  verification: { verdict: string | null; summary: string | null } | null;
  party: { agent_type: string; runs: number; cost_usd: number; seconds: number; failed: number }[];
  /** Runs on a fallback model because their own model's account was paused. Absent on an older backend. */
  fallbacks?: QuestFallback[];
  /** What a paused quest waits for: a spent LLM account, until when. Absent on an older backend. */
  account_wait?: WorldAccountWait | null;
  /** The slopometer's verdict on the job, and a cleanup's diff counts. Absent on an older backend. */
  work_nature?: WorkNature | null;
  xp: { agent_type: string; stat: string; xp: number }[];
  cost_usd: number;
  seconds: number | null;
  started_at: string | null;
  finished_at: string | null;
  evaluation: Evaluation | null;
}

export async function fetchWorld(): Promise<World> {
  const { data } = await api.get<World>("/world");
  return data;
}

export async function fetchCharacter(agentType: string): Promise<CharacterDetail> {
  const { data } = await api.get<CharacterDetail>(`/world/characters/${encodeURIComponent(agentType)}`);
  return data;
}

export async function fetchQuest(jobId: string): Promise<QuestScroll> {
  const { data } = await api.get<QuestScroll>(`/world/quests/${encodeURIComponent(jobId)}`);
  return data;
}

/** Send a party at a quest on the board: the owner proposal's yes, as the signed-in player. */
export async function acceptLead(leadId: string, by: string, runWith?: RunWith): Promise<{ job_id: string | null }> {
  const { data } = await api.post<{ job_id: string | null }>(`/proposals/${encodeURIComponent(leadId)}/accept`, {
    by,
    ...runWithBody(runWith),
  });
  return data;
}

/** A quest the player writes for one character: a job on a repository, on that character's template. */
export async function writeQuest(body: {
  repo: string;
  task: string;
  template: string | null;
  runWith?: RunWith;
}): Promise<{ job_id: string }> {
  const { data } = await api.post<{ job_id: string }>("/jobs", {
    repo: body.repo,
    task: body.task,
    ...(body.template ? { pipeline_template: body.template } : {}),
    ...runWithBody(body.runWith),
  });
  return data;
}

/** Merge a quest's pull request through its merge approval (the dashboard's form). */
export async function approveMerge(approvalId: string): Promise<void> {
  await api.post(`/merge-approvals/${encodeURIComponent(approvalId)}/decide`, { decision: "approve" });
}
