import { api } from "./client";
import type { Forge } from "./repos";

/** What one credential can do at one owner, learned by calling the forge
 * (ADR 0010). `null` means "not probed", never "no". */
export interface OrgAccess {
  identity: string | null;
  owner_kind: string | null;
  read: boolean;
  hooks: boolean | null;
  push: boolean | null;
  repo_count: number | null;
  sample_repo: string | null;
  detail: string;
  /** Where the secret came from: `registry:<label>` or `env:<KEY>`. */
  source?: string;
}

export type OrgStatus = "active" | "paused" | "removed";

export interface Organization {
  owner: string;
  forge: Forge;
  credential_id: string | null;
  display_name: string | null;
  settings: Record<string, unknown>;
  /** Empty until the first probe. */
  access: Partial<OrgAccess>;
  repo_count: number | null;
  discovered_at: string | null;
  status: OrgStatus;
  created_by: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export interface DiscoveryRun {
  id: string;
  owner: string;
  status: "running" | "done" | "error";
  repos_total: number | null;
  repos_seen: number | null;
  summary: Record<string, number>;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

/** One fact row per repository under the owner. `facts` holds what
 * discovery read; a value the credential could not see is `null`. */
export interface OrgRepoFacts {
  owner: string;
  repo: string;
  run_id: string;
  archived: boolean;
  private: boolean;
  fork: boolean;
  default_branch: string | null;
  language: string | null;
  pushed_at: string | null;
  open_issues: number | null;
  facts: Record<string, unknown>;
  seen_at: string | null;
  /** What it takes embry0 to maintain the repository, in one sentence read
   * from the facts by rules (ADR 0015). Absent from an older server. */
  maintain_hint?: string;
  /** The actions that sentence names, in the order they are taken. */
  maintain_steps?: NeedActionType[];
}

export type NeedActionType = "register" | "hook" | "onboard" | "watch" | "setting" | "credential" | "none";
export type NeedStatus = "open" | "done" | "dismissed";

export interface OrgNeed {
  id: string;
  kind: string;
  title: string;
  detail: string;
  repos: string[];
  action: { type: NeedActionType; params: Record<string, unknown> };
  status: NeedStatus;
  case_id: string | null;
}

export interface OrgNeeds {
  owner: string;
  run_id: string | null;
  needs: OrgNeed[];
  counts: { open: number; done: number; dismissed: number };
}

/** The actions the actions endpoint runs. The other need types are done by hand. */
export type OrgActionName = "register" | "hook" | "onboard" | "watch";

export interface OrgActionResult {
  repo: string;
  outcome: "done" | "already" | "skipped" | "failed";
  detail: string;
}

export interface OrgActionResponse {
  owner: string;
  action: OrgActionName;
  dry_run: boolean;
  results: OrgActionResult[];
}

export interface OrgProbeBody {
  owner: string;
  forge: Forge;
  /** Absent: the environment token for this owner. */
  credential_id?: string | null;
}

function httpResponse(err: unknown): { status?: unknown; data?: { detail?: unknown } } | undefined {
  if (typeof err !== "object" || err === null || !("response" in err)) return undefined;
  return (err as { response?: { status?: unknown; data?: { detail?: unknown } } }).response;
}

/** What FastAPI itself answers when no route takes a request: 404 for an
 * unknown path, 405 for a known path without that method. */
const ROUTE_MISS: Readonly<Record<number, string>> = { 404: "Not Found", 405: "Method Not Allowed" };

/** True only when the server has no such route: the needs and actions routes
 * ship separately from this page, so an older server answers with FastAPI's
 * own 404 or 405. A route that exists and answers 404 says what is missing in
 * its own words ("no organization acme", "credential not found"), and that is
 * an error to show. */
export function isMissingEndpoint(err: unknown): boolean {
  const response = httpResponse(err);
  const status = response?.status;
  return typeof status === "number" && status in ROUTE_MISS && response?.data?.detail === ROUTE_MISS[status];
}

export async function fetchOrganizations(): Promise<Organization[]> {
  const { data } = await api.get<{ organizations: Organization[] }>("/orgs");
  return data.organizations;
}

export async function fetchOrganization(owner: string): Promise<Organization> {
  const { data } = await api.get<Organization>(`/orgs/${encodeURIComponent(owner)}`);
  return data;
}

/** Ask the forge what a credential can do at an owner, without storing anything. */
export async function probeOrganization(body: OrgProbeBody): Promise<OrgAccess> {
  const { data } = await api.post<OrgAccess>("/orgs/probe", body, { suppressValidationToast: true });
  return data;
}

/** Register (or update) an owner. The server probes first and refuses an
 * owner the credential cannot list, in the probe's own words. */
export async function registerOrganization(body: OrgProbeBody): Promise<Organization> {
  const { data } = await api.post<Organization>("/orgs", body, { suppressValidationToast: true });
  return data;
}

/** Start a discovery run; it walks the owner in the background (202). */
export async function discoverOrganization(owner: string): Promise<DiscoveryRun> {
  const { data } = await api.post<DiscoveryRun>(`/orgs/${encodeURIComponent(owner)}/discover`);
  return data;
}

/** Discovery runs, newest first. */
export async function fetchOrganizationRuns(owner: string): Promise<DiscoveryRun[]> {
  const { data } = await api.get<{ runs: DiscoveryRun[] }>(`/orgs/${encodeURIComponent(owner)}/runs`);
  return data.runs;
}

export async function fetchOrganizationRepos(owner: string): Promise<OrgRepoFacts[]> {
  const { data } = await api.get<{ repos: OrgRepoFacts[] }>(`/orgs/${encodeURIComponent(owner)}/repos`);
  return data.repos;
}

/** The needs list, or `null` when this server has no needs endpoint yet. */
export async function fetchOrganizationNeeds(owner: string): Promise<OrgNeeds | null> {
  try {
    const { data } = await api.get<OrgNeeds>(`/orgs/${encodeURIComponent(owner)}/needs`);
    return data;
  } catch (err) {
    if (isMissingEndpoint(err)) return null;
    throw err;
  }
}

export async function dismissNeed(owner: string, needId: string): Promise<OrgNeed> {
  const { data } = await api.post<OrgNeed>(
    `/orgs/${encodeURIComponent(owner)}/needs/${encodeURIComponent(needId)}/dismiss`,
  );
  return data;
}

export async function reopenNeed(owner: string, needId: string): Promise<OrgNeed> {
  const { data } = await api.post<OrgNeed>(
    `/orgs/${encodeURIComponent(owner)}/needs/${encodeURIComponent(needId)}/reopen`,
  );
  return data;
}

/** Run one action on a set of repositories. Idempotent on the server: a
 * second identical call answers "already" for every repository. */
export async function runOrganizationAction(
  owner: string,
  body: { action: OrgActionName; repos: string[]; dry_run: boolean },
): Promise<OrgActionResponse> {
  const { data } = await api.post<OrgActionResponse>(`/orgs/${encodeURIComponent(owner)}/actions`, body, {
    suppressValidationToast: true,
  });
  return data;
}
