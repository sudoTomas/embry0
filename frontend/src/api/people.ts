import { api } from "./client";

/** The kinds of identity a person may have: a login on a forge, or a handle in a chat (#179). */
export const FORGE_KINDS = ["github", "gitlab"] as const;
export const CHAT_KINDS = ["telegram", "slack", "teams", "discord"] as const;
export const IDENTITY_KINDS = [...FORGE_KINDS, ...CHAT_KINDS] as const;
export type IdentityKind = (typeof IDENTITY_KINDS)[number];

export interface Identity {
  kind: IdentityKind;
  value: string;
}

/**
 * A person of the people directory (#179): who embry0 asks and names as an owner, whether or not they sign in.
 * Not the hand-off list's `Person` (api/handoffs.ts), which is a user who signs in.
 */
export interface DirectoryPerson {
  id: string;
  name: string;
  email: string | null;
  /** The users id they sign in as, when they do. */
  user_id: string | null;
  /** IANA time zone; null reads their organization's. */
  timezone: string | null;
  /** "09-17"; null reads their organization's. */
  hours: string | null;
  /** "mon-fri"; null reads their organization's. */
  weekdays: string | null;
  disabled: boolean;
  identities: Identity[];
  /** The version a change names in If-Match. */
  updated_at: string | null;
}

/** What a write sends: every field, null clearing an optional one, and the identities made exactly the person's. */
export interface PersonWrite {
  name: string;
  email: string | null;
  user_id: string | null;
  timezone: string | null;
  hours: string | null;
  weekdays: string | null;
  disabled: boolean;
  identities: Identity[];
}

/** Someone who could be added (GET /people/suggestions): a user who signs in with no person. */
export interface SuggestedUser {
  user_id: string;
  email: string;
  name: string;
}

/** An owner login no person holds, on its repository's organization's forge, with the repositories naming it. */
export interface SuggestedOwner {
  forge: string;
  login: string;
  repos: string[];
}

export interface PeopleSuggestions {
  users: SuggestedUser[];
  owners: SuggestedOwner[];
}

export async function fetchDirectory(includeDisabled: boolean): Promise<DirectoryPerson[]> {
  const { data } = await api.get<{ people: DirectoryPerson[] }>("/people", {
    params: includeDisabled ? { include_disabled: true } : {},
  });
  return data.people;
}

export async function fetchSuggestions(): Promise<PeopleSuggestions> {
  const { data } = await api.get<PeopleSuggestions>("/people/suggestions");
  return data;
}

export async function createPerson(body: PersonWrite): Promise<DirectoryPerson> {
  const { data } = await api.post<DirectoryPerson>("/people", body, { suppressValidationToast: true });
  return data;
}

/** Change a person, guarded by the version read (If-Match: the API refuses a change made blind, 428, and one made
 * over a newer version, 409). */
export async function updatePerson(person: DirectoryPerson, body: PersonWrite): Promise<DirectoryPerson> {
  const { data } = await api.patch<DirectoryPerson>(`/people/${encodeURIComponent(person.id)}`, body, {
    headers: { "If-Match": `"${person.updated_at ?? ""}"` },
    suppressValidationToast: true,
  });
  return data;
}

export async function removePerson(personId: string): Promise<void> {
  await api.delete(`/people/${encodeURIComponent(personId)}`);
}
