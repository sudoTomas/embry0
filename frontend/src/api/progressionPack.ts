import { api } from "./client";
import type { Progression } from "./progression";

/**
 * The progression content pack (#176): the game's skills, rates, tiers,
 * achievements, quests and words as one YAML document an admin edits. With
 * nothing stored, or after a reset, the deployment follows the shipped
 * default. Every route is `/progression/pack…`, except the preview.
 */

/** One problem of a refused pack, in FastAPI's shape: `loc` is the path from the document's root. */
export interface PackProblem {
  loc: (string | number)[];
  type: string;
  msg: string;
}

/** Where a version came from. */
export type PackSource = "person" | "file" | "restore" | "reset" | "upgrade";

/** The pack in effect (`GET /progression/pack`). */
export interface PackState {
  /** 0 when nothing is stored. */
  version: number;
  follows_default: boolean;
  schema_version: number;
  hash: string;
  /** The text in effect: the stored one, or the shipped default's. */
  text: string;
  based_on: string | null;
  default_hash: string;
  /** The shipped default changed since this customised pack was taken from it. */
  default_moved: boolean;
  source: PackSource | null;
  reason: string;
  /** Who activated it; null for someone else unless the caller is an admin (as `GET /audit` shows actors). */
  created_by: string | null;
  activated_at: string | null;
  /** Why the stored version is not in effect (this release refuses it); the shipped default is then. */
  fallback: PackProblem[] | null;
  /** The draft's version, when there is one. */
  draft_version: number | null;
}

/** A version of the pack, as the history lists it. */
export interface PackVersion {
  version: number;
  status: "draft" | "active" | "superseded";
  follows_default: boolean;
  schema_version: number;
  based_on: string | null;
  source: PackSource;
  reason: string;
  /** Who wrote it; null for someone else unless the caller is an admin (as `GET /audit` shows actors). */
  created_by: string | null;
  created_at: string | null;
  activated_at: string | null;
  /** The active version it was written over (0: nothing was stored); older backends leave it out. */
  parent_version?: number;
}

/** The one draft (`GET /progression/pack/draft`). */
export interface PackDraft extends PackVersion {
  text: string;
  /** Null when this release refuses the draft (`problems`). */
  hash: string | null;
  /** The sections it changes against the pack in effect. */
  sections: string[];
  problems: PackProblem[] | null;
  /**
   * The pack in effect changed since this draft was saved over `parent_version`:
   * activating it is refused until it is saved again over the pack in effect.
   */
  stale?: boolean;
}

export interface SavedDraft {
  version: number;
  replaced_version: number | null;
  hash: string;
  based_on: string | null;
  sections: string[];
  warnings: string[];
}

/** What a dry run of `PUT /progression/pack` would do. */
export interface PackCheck {
  would_change: boolean;
  version: number;
  hash: string;
  sections: string[];
  text_diff: string;
  warnings: string[];
}

/** What an activating write did. */
export interface PackActivation {
  changed: boolean;
  version: number;
  previous_version: number | null;
  source: PackSource | null;
  hash: string;
  follows_default: boolean;
  warnings: string[];
}

export interface DefaultDiff {
  based_on: string | null;
  default_hash: string;
  moved: boolean;
  /** From the default the pack was based on to this release's; null when not recorded. */
  text_diff: string | null;
}

export interface CharacterLevels {
  level: number;
  total_level: number;
  xp: number;
}

/** What the draft re-prices: before is the pack in effect, after the draft. */
export interface PackChanges {
  characters: { agent_type: string; before: CharacterLevels | null; after: CharacterLevels | null }[];
  account: { before: { total_level: number; xp: number }; after: { total_level: number; xp: number } };
  achievements_unlocked: { before: number; after: number };
}

/** `GET /progression/preview`: the progression under the draft, with what it changes. */
export type PackPreview = Progression & {
  draft: { version: number; hash: string; parent_version?: number; stale?: boolean };
  active: { hash: string; version?: number };
  changes: PackChanges;
};

const YAML = { "Content-Type": "application/yaml" };

/** `If-Match` for a version (0 names "nothing stored"). */
function ifMatch(version: number | null | undefined): Record<string, string> {
  return version === null || version === undefined ? {} : { "If-Match": `"${version}"` };
}

export async function fetchPack(): Promise<PackState> {
  const { data } = await api.get<PackState>("/progression/pack");
  return data;
}

/** The draft, or null when there is none. */
export async function fetchPackDraft(): Promise<PackDraft | null> {
  const { data, status } = await api.get<PackDraft>("/progression/pack/draft", {
    validateStatus: (code) => code === 200 || code === 404,
  });
  return status === 404 ? null : data;
}

export async function fetchDefaultDiff(): Promise<DefaultDiff> {
  const { data } = await api.get<DefaultDiff>("/progression/pack/default/diff");
  return data;
}

export async function fetchPackVersions(): Promise<PackVersion[]> {
  const { data } = await api.get<{ versions: PackVersion[] }>("/progression/pack/versions");
  return data.versions;
}

export async function fetchPackVersion(version: number): Promise<PackVersion & { text: string | null }> {
  const { data } = await api.get<PackVersion & { text: string | null }>(`/progression/pack/versions/${version}`);
  return data;
}

/** A dry run of the one-shot put: problems (a 422), or what activating this text would change. Stores nothing. */
export async function checkPack(text: string): Promise<PackCheck> {
  const { data } = await api.put<PackCheck>("/progression/pack", text, {
    params: { dry_run: 1 },
    headers: YAML,
    suppressValidationToast: true,
  });
  return data;
}

export interface SaveDraftRequest {
  text: string;
  /** The draft this replaces; 0 when there is none. */
  draftVersion: number;
  /** The text now takes in this release's shipped default. */
  rebase?: boolean;
  reason?: string;
}

export async function savePackDraft({ text, draftVersion, rebase, reason }: SaveDraftRequest): Promise<SavedDraft> {
  const params: Record<string, string | number> = {};
  if (rebase) params.rebase = 1;
  if (reason) params.reason = reason;
  const { data } = await api.put<SavedDraft>("/progression/pack/draft", text, {
    params,
    headers: { ...YAML, ...ifMatch(draftVersion) },
    suppressValidationToast: true,
  });
  return data;
}

export async function deletePackDraft(draftVersion: number): Promise<{ deleted_version: number }> {
  const { data } = await api.delete<{ deleted_version: number }>("/progression/pack/draft", {
    headers: ifMatch(draftVersion),
  });
  return data;
}

/** The history re-priced under the draft. One runs at a time (a 429 while another does). */
export async function previewPackDraft(): Promise<PackPreview> {
  // It reads the whole history, so it may take longer than an ordinary read.
  const { data } = await api.get<PackPreview>("/progression/preview", {
    timeout: 120_000,
    suppressValidationToast: true,
  });
  return data;
}

export async function activatePackDraft(draftVersion: number, reason?: string): Promise<PackActivation> {
  const { data } = await api.post<PackActivation>("/progression/pack/draft/activate", null, {
    params: reason ? { reason } : {},
    headers: ifMatch(draftVersion),
    suppressValidationToast: true,
  });
  return data;
}

export async function restorePackVersion(version: number, activeVersion: number): Promise<PackActivation> {
  const { data } = await api.post<PackActivation>(`/progression/pack/versions/${version}/restore`, null, {
    headers: ifMatch(activeVersion),
    suppressValidationToast: true,
  });
  return data;
}

export async function resetPack(activeVersion: number): Promise<PackActivation> {
  const { data } = await api.post<PackActivation>("/progression/pack/reset", null, {
    headers: ifMatch(activeVersion),
  });
  return data;
}
