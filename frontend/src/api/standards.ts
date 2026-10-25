import { api } from "./client";
import { type RunWith, runWithBody } from "./runWith";

/**
 * Standards (ADR 0014): what an owner holds every repository under it to,
 * versioned per owner and kind, with each repository's mode and newest audit.
 * docs/standards.md is the contract.
 */

export type StandardKind = "design";
export type StandardMode = "off" | "advise" | "enforce";

/** The modes in the order an admin picks from them. */
export const STANDARD_MODES: readonly StandardMode[] = ["off", "advise", "enforce"];

export interface StandardRule {
  id: string;
  text: string;
  [key: string]: unknown;
}

/** The shape every standard of a kind shares. Each key may be absent. */
export interface StandardSchema {
  roles?: Record<string, Record<string, unknown>>;
  type_scale?: Record<string, Record<string, unknown>>;
  rules?: StandardRule[];
}

export interface Standard {
  id: string;
  owner: string;
  kind: string;
  version: number;
  /** `draft`, `active` or `superseded`; a string so a newer status still renders. */
  status: string;
  schema: StandardSchema;
  /** The owner's fill of the roles: role name to any JSON value. */
  values: Record<string, unknown>;
  prose: string;
  references: string[];
  created_by: string | null;
  created_at: string | null;
}

export interface StandardException {
  rule: string;
  reason: string;
  paths: string[];
}

/** A repository's place under its owner's standard of one kind. `score` and
 * `findings_open` come from the newest audit that completed (`last_audit_at`);
 * `last_audit_job_id` is the newest audit started, which may still run. */
export interface RepoStandardState {
  repo: string;
  owner: string;
  kind: string;
  mode: StandardMode;
  last_audit_at: string | null;
  last_audit_job_id: string | null;
  score: number | null;
  findings_open: number | null;
  exceptions: StandardException[];
  updated_at: string | null;
}

export interface OwnerStandards {
  owner: string;
  kinds: string[];
  modes: StandardMode[];
  /** The active version of each kind that has one. */
  standards: Standard[];
  /** Every repository under the owner with a stored mode. */
  repos: RepoStandardState[];
}

export interface AuditStarted {
  job_id: string;
  owner: string;
  kind: string;
  repo: string;
  version: number;
  template: string;
}

const enc = encodeURIComponent;

/** A repository path with each segment encoded and the slashes kept: the
 * routes take `{repo:path}`, and a GitLab repository may have more than two. */
export function repoPathParam(repo: string): string {
  return repo.split("/").map(enc).join("/");
}

/** Where an admin writes a new version. The dashboard ships no editor for
 * the standard's JSON, so the page names this path instead. */
export function standardApiPath(owner: string, kind: string): string {
  return `/api/v1/standards/${enc(owner)}/${enc(kind)}`;
}

export async function fetchOwnerStandards(owner: string): Promise<OwnerStandards> {
  const { data } = await api.get<OwnerStandards>(`/standards/${enc(owner)}`);
  return data;
}

/** Every version, newest first. */
export async function fetchStandardVersions(owner: string, kind: string): Promise<Standard[]> {
  const { data } = await api.get<{ versions: Standard[] }>(`/standards/${enc(owner)}/${enc(kind)}/versions`);
  return data.versions;
}

/** Set a repository's mode (admin). Exceptions are left out, so the server keeps them. */
export async function setRepoStandardMode(
  owner: string,
  kind: string,
  repo: string,
  mode: StandardMode,
): Promise<RepoStandardState> {
  const { data } = await api.put<RepoStandardState>(
    `/standards/${enc(owner)}/${enc(kind)}/repos/${repoPathParam(repo)}`,
    { mode },
    { suppressValidationToast: true },
  );
  return data;
}

/** Start a read-only audit job (operator). A 409 says why in its detail. */
export async function startStandardAudit(
  owner: string,
  kind: string,
  repo: string,
  runWith?: RunWith,
): Promise<AuditStarted> {
  const body = runWithBody(runWith);
  const { data } = await api.post<AuditStarted>(
    `/standards/${enc(owner)}/${enc(kind)}/audit/${repoPathParam(repo)}`,
    Object.keys(body).length ? body : undefined,
    { suppressValidationToast: true },
  );
  return data;
}
