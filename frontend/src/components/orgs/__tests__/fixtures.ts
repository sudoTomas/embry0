import type { DiscoveryRun, OrgAccess, OrgNeed, OrgNeeds, OrgRepoFacts, Organization } from "@/api/orgs";

export const ACCESS: OrgAccess = {
  identity: "acme-bot",
  owner_kind: "organization",
  read: true,
  hooks: true,
  push: null,
  repo_count: 12,
  sample_repo: "acme/widgets",
  detail: "token is acme-bot; acme is an organization; can list repositories; can manage webhooks; 12 repositories",
  source: "env:GITHUB_TOKEN__ACME",
};

export function org(overrides: Partial<Organization> = {}): Organization {
  return {
    owner: "acme",
    forge: "github",
    credential_id: null,
    display_name: null,
    settings: {},
    access: ACCESS,
    repo_count: 12,
    discovered_at: null,
    status: "active",
    created_by: null,
    created_at: null,
    updated_at: null,
    ...overrides,
  };
}

export function run(overrides: Partial<DiscoveryRun> = {}): DiscoveryRun {
  return {
    id: "odr-1",
    owner: "acme",
    status: "done",
    repos_total: 2,
    repos_seen: 2,
    summary: { repos: 2, archived: 0, registered: 1, hooked: 1, qa_config: 1, watched: 0 },
    error: null,
    started_at: null,
    finished_at: null,
    ...overrides,
  };
}

export function repo(name: string, facts: Record<string, unknown> = {}, overrides: Partial<OrgRepoFacts> = {}): OrgRepoFacts {
  return {
    owner: "acme",
    repo: `acme/${name}`,
    run_id: "odr-1",
    archived: false,
    private: false,
    fork: false,
    default_branch: "main",
    language: "TypeScript",
    pushed_at: null,
    open_issues: 3,
    facts: {
      root_visible: true,
      hook_present: false,
      registered: false,
      qa_config: false,
      watcher_stack: false,
      tracker_mapped: false,
      frontend: false,
      boot_hint: null,
      ...facts,
    },
    seen_at: null,
    ...overrides,
  };
}

export function need(id: string, kind: string, repos: string[], overrides: Partial<OrgNeed> = {}): OrgNeed {
  const type =
    kind === "not_registered" ? "register" : kind === "no_webhook" ? "hook" : kind === "not_watched" ? "watch" : "none";
  return {
    id,
    kind,
    title: `${kind} ${id}`,
    detail: "",
    repos,
    action: { type, params: {} },
    status: "open",
    case_id: null,
    ...overrides,
  };
}

export function needs(list: OrgNeed[]): OrgNeeds {
  return {
    owner: "acme",
    run_id: "odr-1",
    needs: list,
    counts: {
      open: list.filter((n) => n.status === "open").length,
      done: list.filter((n) => n.status === "done").length,
      dismissed: list.filter((n) => n.status === "dismissed").length,
    },
  };
}
