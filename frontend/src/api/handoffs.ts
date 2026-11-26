import { api } from "./client";

/** Someone a decision can be sent to (hand-offs, spec 2026-10-27): who signs in to embry0, or a person (#179). */
export interface Person {
  /** Their sign-in's id, else their person's. */
  id: string;
  email: string;
  name: string;
  /** Their sign-in's tier; empty for a person who does not sign in. */
  tier: string;
}

/** An open hand-off as a decision carries it. */
export interface Assignee {
  handoff_id: string;
  email: string;
  name: string;
  /** Who sent it. */
  from: string;
  note: string;
  at: string;
}

export interface Handoff {
  id: string;
  decision_id: string;
  repo: string;
  title: string;
  from_actor: string;
  to_email: string;
  note: string;
  state: "open" | "decided" | "returned" | "passed" | "cancelled";
  outcome: string | null;
  created_at: string;
  closed_at: string | null;
}

export async function fetchPeople(): Promise<Person[]> {
  const { data } = await api.get<{ people: Person[] }>("/handoffs/people");
  return data.people;
}

export async function sendDecision(decisionId: string, to: string, note: string): Promise<Handoff> {
  const { data } = await api.post<Handoff>(`/decisions/${encodeURIComponent(decisionId)}/send-to`, { to, note });
  return data;
}

export async function returnHandoff(handoffId: string, note: string): Promise<Handoff> {
  const { data } = await api.post<Handoff>(`/handoffs/${encodeURIComponent(handoffId)}/return`, { note });
  return data;
}
