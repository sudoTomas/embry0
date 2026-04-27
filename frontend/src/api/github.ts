import { api } from "./client";

export interface GitHubRepo {
  full_name: string;
  description: string | null;
  private: boolean;
  html_url: string;
  default_branch: string;
  language: string | null;
  open_issues_count: number;
}

export interface GitHubRepoListResponse {
  repos: GitHubRepo[];
}

export async function fetchGitHubRepos(): Promise<GitHubRepoListResponse> {
  const { data } = await api.get("/github/repos");
  return data;
}
