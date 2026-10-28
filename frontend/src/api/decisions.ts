import { api } from "./client";
import type { Assignee } from "./handoffs";

/** How a verdict or a fact leans the decision (decisions, spec 2026-10-27). */
export type DecisionTone = "good" | "warn" | "bad" | "info";

/** Every answer a decision can list; each one runs on its own route. */
export type DecisionAction =
  | "start"
  | "decline"
  | "later"
  | "send_to"
  | "merge"
  | "send_back"
  | "reject"
  | "run_again"
  | "good"
  | "bad"
  | "dismiss";

export interface DecisionFact {
  label: string;
  value: string;
  tone: "" | "good" | "warn" | "bad";
}

/**
 * One choice waiting on a person, built by the server so the dashboard and a
 * chat show the same card: a quest on offer (`offer:<proposal id>`) or a quest
 * to review (`done:<job id>`).
 */
export interface Decision {
  id: string;
  stage: "offered" | "done";
  /** The proposal kind on offer; the job's template when done. */
  kind: string;
  kind_label: string;
  verdict: { label: string; tone: DecisionTone };
  title: string;
  repo: string;
  area: string | null;
  at: string | null;
  /** At most six, in a fixed order, none empty. */
  facts: DecisionFact[];
  why: string;
  /** What accepting starts, in words (on offer only). */
  yes_starts: string | null;
  actions: DecisionAction[];
  owners: string[];
  /** Shown on the page but not asked in a chat. */
  held: boolean;
  /** The open hand-off: who has it to decide, from whom, and the note. */
  assigned_to: Assignee | null;
  links: { proposal?: string; url?: string; job?: string; pr?: string; merge_approval?: string };
  details: string;
}

export interface DecisionFilters {
  stage?: "offered" | "done" | "all";
  owner?: string;
  repo?: string;
  include_held?: boolean;
  /** me: handed to the viewer, or on offer and owned by their GitHub login. */
  for?: "me" | "all";
}

export async function fetchDecisions(params: DecisionFilters): Promise<Decision[]> {
  const { data } = await api.get<{ decisions: Decision[] }>("/decisions", { params });
  return data.decisions;
}

export async function fetchDecision(id: string): Promise<Decision> {
  const { data } = await api.get<Decision>(`/decisions/${encodeURIComponent(id)}`);
  return data;
}
