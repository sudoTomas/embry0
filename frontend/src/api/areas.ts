import { api } from "./client";
import { type RunWith, runWithBody } from "./runWith";

/** One part of a monorepo, as its `.embry0/areas.yml` names it (spec 2026-10-25). */
export interface RepoArea {
  name: string;
  slug: string;
  paths: string[];
  logs: string[];
  owners: string[];
}

export interface RepoAreas {
  repo: string;
  path: string;
  areas: RepoArea[];
  /** The newest map-areas job: what the agent did last, and its pull request. */
  latest_job: {
    job_id: string;
    status: string;
    created_at: string | null;
    finished_at: string | null;
    pr_url: string | null;
    live: boolean;
  } | null;
}

export async function fetchRepoAreas(fullRepo: string, fresh = false): Promise<RepoAreas> {
  const { data } = await api.get<RepoAreas>(`/repos/${fullRepo}/areas`, { params: fresh ? { fresh: true } : {} });
  return data;
}

/** Start the agent that writes the file and opens its pull request. 409 while a map is running. */
export async function startMapAreas(fullRepo: string, runWith?: RunWith): Promise<{ job_id: string }> {
  const { data } = await api.post<{ job_id: string }>(`/repos/${fullRepo}/areas/map`, runWithBody(runWith), {
    suppressValidationToast: true,
  });
  return data;
}
