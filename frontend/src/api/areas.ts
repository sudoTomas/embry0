import { api } from "./client";
import { isNotFound } from "./errorDetail";
import { repoPath } from "./repoPath";
import { type RunWith, runWithBody } from "./runWith";

/** One part of a monorepo, as the repository's areas document names it (spec 2026-11-10). */
export interface RepoArea {
  name: string;
  slug: string;
  paths: string[];
  logs: string[];
  owners: string[];
}

export type AreasProposalStatus = "running" | "pending" | "applied" | "rejected" | "stale" | "failed" | "unchanged";

/** The newest map of the repository's areas, as the areas read names it. */
export interface AreasProposalSummary {
  id: string;
  status: AreasProposalStatus;
  job_id: string | null;
  at: string | null;
}

/** A repository's areas as embry0 keeps them: the document as written and the areas it names. */
export interface RepoAreas {
  repo: string;
  /** The document as written, comments included; empty when the repository names no areas. */
  yaml_text: string;
  areas: RepoArea[];
  /** The version a write names in If-Match; 0 when nothing is stored. */
  generation: number;
  updated_by: string | null;
  updated_at: string | null;
  /** When the repository's old `.embry0/areas.yml` was imported, once; null when it was not. */
  imported: { outcome: string; at: string | null; blob_sha: string | null } | null;
  /** The newest map-areas job. */
  latest_job: {
    job_id: string;
    status: string;
    created_at: string | null;
    finished_at: string | null;
    pr_url: string | null;
    live: boolean;
  } | null;
  proposal: AreasProposalSummary | null;
}

/** One area a change adds, removes or changes; the owners it adds or removes are named apart. */
export interface AreaChange {
  area: string;
  change: "added" | "removed" | "changed";
  keys: string[];
  owners_added: string[];
  owners_removed: string[];
}

export interface AreasSaveResult {
  repo: string;
  generation: number;
  /** Null when nothing changed, or for a preview. */
  revision_id: number | null;
  changes: AreaChange[];
  /** A preview's: whether saving would change the stored text, and how. */
  would_change?: boolean;
  text_diff?: string;
}

/** A map of the repository's areas, waiting for a person or decided. */
export interface AreasProposal {
  id: string;
  repo: string;
  status: AreasProposalStatus;
  job_id: string | null;
  job_status: string | null;
  base_generation: number;
  /** The areas changed since the map started: applying it is refused. */
  stale: boolean;
  proposed_text: string | null;
  changes: AreaChange[];
  text_diff: string;
  notes_md: string;
  failure: string;
  created_by: string | null;
  created_at: string | null;
  decided_by: string | null;
  decided_at: string | null;
}

// A write sends the YAML as written, so the stored document keeps its comments. A 422 lists every problem by
// its key; the editor shows them next to the text, so the global validation toast is suppressed.
const YAML_BODY = { headers: { "Content-Type": "application/yaml" }, suppressValidationToast: true };

export async function fetchRepoAreas(fullRepo: string): Promise<RepoAreas> {
  const { data } = await api.get<RepoAreas>(repoPath(fullRepo, "areas"));
  return data;
}

/** What saving `yamlText` would change; stores nothing. 409 when `generation` is no longer the stored one. */
export async function previewRepoAreas(fullRepo: string, yamlText: string, generation: number): Promise<AreasSaveResult> {
  const { data } = await api.put<AreasSaveResult>(repoPath(fullRepo, "areas"), yamlText, {
    ...YAML_BODY,
    headers: { ...YAML_BODY.headers, "If-Match": `"${generation}"` },
    params: { dry_run: 1 },
  });
  return data;
}

/** Replace the repository's areas. 409 when `generation` is no longer the stored one (0 creates them). */
export async function saveRepoAreas(fullRepo: string, yamlText: string, generation: number): Promise<AreasSaveResult> {
  const { data } = await api.put<AreasSaveResult>(repoPath(fullRepo, "areas"), yamlText, {
    ...YAML_BODY,
    headers: { ...YAML_BODY.headers, "If-Match": `"${generation}"` },
  });
  return data;
}

/** Start the agent that maps the areas; its document waits for a person. 409 while a map is running. */
export async function startMapAreas(
  fullRepo: string,
  runWith?: RunWith,
): Promise<{ job_id: string; repo: string; proposal_id: string }> {
  const { data } = await api.post<{ job_id: string; repo: string; proposal_id: string }>(
    repoPath(fullRepo, "areas/map"),
    runWithBody(runWith),
    { suppressValidationToast: true },
  );
  return data;
}

/** The newest map of the repository's areas; null when it was never mapped. */
export async function fetchAreasProposal(fullRepo: string): Promise<AreasProposal | null> {
  try {
    const { data } = await api.get<AreasProposal>(repoPath(fullRepo, "areas/proposal"));
    return data;
  } catch (error) {
    if (isNotFound(error)) return null;
    throw error;
  }
}

/** Store a waiting map as the areas. 409 when the areas changed since the map started (it is then stale). */
export async function applyAreasProposal(fullRepo: string, proposalId: string): Promise<{ revision_id: number | null }> {
  const { data } = await api.post<{ revision_id: number | null }>(
    repoPath(fullRepo, `areas/proposal/${encodeURIComponent(proposalId)}/apply`),
  );
  return data;
}

export async function rejectAreasProposal(fullRepo: string, proposalId: string): Promise<void> {
  await api.post(repoPath(fullRepo, `areas/proposal/${encodeURIComponent(proposalId)}/reject`));
}

/** A map waiting for a person (GET /areas/proposals). */
export interface PendingAreasProposal {
  id: string;
  repo: string;
  created_at: string;
}

export async function fetchPendingAreasProposals(): Promise<PendingAreasProposal[]> {
  const { data } = await api.get<{ proposals: PendingAreasProposal[] }>("/areas/proposals", {
    params: { status: "pending" },
  });
  return data.proposals;
}
