import { api } from "./client";

/** Someone who signs in to embry0, and so can be sent a decision (hand-offs, spec 2026-10-27). */
export interface Person {
  id: string;
  email: string;
  name: string;
  /** The GitHub login owners are named by; null until set. */
  github_login: string | null;
  tier: string;
}

/** An open hand-off as a decision carries it. */
export interface Assignee {
  handoff_id: string;
  email: string;
  name: string;
  github_login: string | null;
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
  const { data } = await api.get<{ people: Person[] }>("/people");
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

export async function setMyGithubLogin(login: string): Promise<Person> {
  const { data } = await api.put<Person>("/me/github-login", { login });
  return data;
}
