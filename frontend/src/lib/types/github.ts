export interface RepoResponse {
  full_name: string;
  description: string | null;
  private: boolean;
  html_url: string;
  default_branch: string;
  language: string | null;
  open_issues_count: number;
}

export interface RepoListResponse {
  repos: RepoResponse[];
}

export interface GithubIssueResponse {
  number: number;
  title: string;
  body: string | null;
  state: string;
  html_url: string;
  labels: string[];
  user: string;
  created_at: string;
  updated_at: string;
}

export interface GithubIssueListResponse {
  issues: GithubIssueResponse[];
  total: number;
}

export interface GithubIssueCreateRequest {
  title: string;
  body?: string;
  labels?: string[];
}
