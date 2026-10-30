import { api } from "./client";
import { repoPath } from "./repoPath";
import type { RepoStandardState } from "./standards";

/** Registry row from GET /repos (RAV-1251; env counts added in RAV-1294). */
export type Forge = "github" | "gitlab";

export interface RegisteredRepo {
  repo: string;
  /** Which SCM host this repo lives on (#162). */
  forge: Forge;
  active: boolean;
  notes: string;
  /** #522: an admin let this repo's sandboxes have the shared Docker daemon. */
  docker_access: boolean;
  created_at: string;
  updated_at: string;
  has_qa_config: boolean;
  env_var_count: number;
  env_secret_count: number;
}

/** Unified per-repo view from GET /repos/{full_repo}/-/. `repo` may contain
 *  more than two segments (GitLab nested subgroups, #162). */
export interface RepoDetail extends RegisteredRepo {
  qa_config_updated_at: string | null;
  qa_config_updated_by: string | null;
  preferences: Record<string, unknown> | null;
  /** One state per kind of the owner's standards (ADR 0014); absent from an older server. */
  standards?: RepoStandardState[];
}

export async function fetchRepos(includeInactive = false): Promise<RegisteredRepo[]> {
  const { data } = await api.get<RegisteredRepo[]>("/repos", {
    params: includeInactive ? { include_inactive: true } : undefined,
  });
  return data;
}

export async function fetchRepoDetail(fullRepo: string): Promise<RepoDetail> {
  const { data } = await api.get<RepoDetail>(repoPath(fullRepo));
  return data;
}

export async function registerRepo(repo: string, notes = "", forge: Forge = "github"): Promise<RegisteredRepo> {
  const { data } = await api.post<RegisteredRepo>("/repos", { repo, notes, forge });
  return data;
}

export async function setRepoActive(fullRepo: string, active: boolean): Promise<RegisteredRepo> {
  const { data } = await api.put<RegisteredRepo>(repoPath(fullRepo, "active"), { active });
  return data;
}

/** #522: grant or revoke the shared Docker daemon for one repo. Admin-tier. */
export async function setRepoDockerAccess(fullRepo: string, enabled: boolean): Promise<RegisteredRepo> {
  const { data } = await api.put<RegisteredRepo>(repoPath(fullRepo, "docker-access"), { enabled });
  return data;
}
