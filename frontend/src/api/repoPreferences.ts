import { api } from "./client";

export interface RepoPreferences {
  repo: string;
  sandbox_profile: string | null;
  language_hint: string | null;
  notes: string;
  updated_at: string;
}

export interface RepoPreferencesUpdate {
  sandbox_profile?: string | null;
  language_hint?: string | null;
  notes?: string;
}

export async function fetchRepoPreferences(
  owner: string,
  repo: string,
): Promise<RepoPreferences | null> {
  const { data } = await api.get<RepoPreferences | null>(`/repos/${owner}/${repo}/preferences`);
  return data;
}

export async function setRepoPreferences(
  owner: string,
  repo: string,
  update: RepoPreferencesUpdate,
): Promise<RepoPreferences> {
  const { data } = await api.put<RepoPreferences>(`/repos/${owner}/${repo}/preferences`, update);
  return data;
}

export async function deleteRepoPreferences(owner: string, repo: string): Promise<void> {
  await api.delete(`/repos/${owner}/${repo}/preferences`);
}
