import { useMemo, useState } from "react";
import { useNavigate } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { setRepoCredentials } from "@/api/credentials";
import { fetchDiscoverableRepos, type DiscoverableRepo } from "@/api/github";
import { fetchDiscoverableProjects, type GitLabProject } from "@/api/gitlab";
import { fetchRepoDetail, registerRepo, type Forge, type RegisteredRepo } from "@/api/repos";
import { useDiscoverableRepos } from "@/hooks/useGitHub";
import { useDiscoverableProjects } from "@/hooks/useGitLab";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";

// RAV-1301: the central Add-repo flow. The dropdown is fed by the discovery
// endpoint — everything the configured GitHub tokens can see minus the
// registry — so it doubles as a capability statement (fine-grained PATs are
// single-owner and repo-scoped). Manual entry stays as an escape hatch for
// repos the tokens can't enumerate yet, clearly marked unverified.

// Mirrors the backend's repo validator (embry0/api/v1/repos.py). TWO OR MORE
// segments since #162 — a GitLab nested subgroup like
// acme/widgets/shop has three and the old two-segment form rejected it.
const SEGMENT = "[A-Za-z0-9][A-Za-z0-9_.-]*";
const REPO_PATTERN = new RegExp(`^${SEGMENT}(?:/${SEGMENT})+$`);

function httpStatus(err: unknown): number | undefined {
  if (typeof err !== "object" || err === null || !("response" in err)) return undefined;
  const status = (err as { response?: { status?: unknown } }).response?.status;
  return typeof status === "number" ? status : undefined;
}

function projectLabel(p: GitLabProject): string {
  const parts = [p.private ? "private" : "public", `default ${p.default_branch}`];
  if (p.credential_id) parts.push(p.credential_label);
  return `${p.full_name} (${parts.join(", ")})`;
}

function repoLabel(r: DiscoverableRepo): string {
  // A registry row names the account. An env row still names the key, never the value.
  const account = r.credential_id ? r.credential_label : r.token_env_key;
  const parts = [r.private ? "private" : "public", account];
  if (r.push === false) parts.push("push?");
  return `${r.full_name} (${parts.join(", ")})`;
}

const BIND_FORBIDDEN = "Registered, but binding the account needs admin scope";

type RegisterVars = { target: string; credentialId: string | null };

type RegisterOutcome =
  | { existed: true }
  | { existed: false; row: RegisteredRepo; bound: boolean; forbidden: boolean };

export function AddRepoPanel() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const discovery = useDiscoverableRepos();
  const gitlabDiscovery = useDiscoverableProjects();
  const gitlabConfigured = gitlabDiscovery.data?.configured ?? false;

  const [forge, setForge] = useState<Forge>("github");
  const [selected, setSelected] = useState("");
  const [manualMode, setManualMode] = useState(false);
  const [manualRepo, setManualRepo] = useState("");

  const repo = manualMode ? manualRepo.trim() : selected;
  const repoValid = REPO_PATTERN.test(repo);

  // Group by top-level namespace for both forges. GitLab nests deeper, but
  // the first segment is the group a token is issued against, which is the
  // useful grouping for an operator scanning the list.
  const grouped = useMemo(() => {
    const entries: { full_name: string; label: string }[] =
      forge === "gitlab"
        ? (gitlabDiscovery.data?.projects ?? []).map((p: GitLabProject) => ({
            full_name: p.full_name,
            label: projectLabel(p),
          }))
        : (discovery.data?.repos ?? []).map((r: DiscoverableRepo) => ({
            full_name: r.full_name,
            label: repoLabel(r),
          }));
    const groups = new Map<string, { full_name: string; label: string }[]>();
    for (const e of entries) {
      const owner = e.full_name.split("/")[0];
      const list = groups.get(owner) ?? [];
      list.push(e);
      groups.set(owner, list);
    }
    return [...groups.entries()];
  }, [forge, discovery.data, gitlabDiscovery.data]);

  const selectedEntry = discovery.data?.repos.find((r) => r.full_name === selected);

  function credentialIdFor(target: string): string | null {
    if (manualMode) return null;
    const id =
      forge === "gitlab"
        ? gitlabDiscovery.data?.projects.find((p) => p.full_name === target)?.credential_id
        : discovery.data?.repos.find((r) => r.full_name === target)?.credential_id;
    return id || null;
  }

  async function registerAndBind(vars: RegisterVars): Promise<RegisterOutcome> {
    // POST /repos inserts on conflict and still returns the row, so binding
    // here would replace every role on a repo that already exists.
    try {
      await fetchRepoDetail(vars.target);
    } catch (err) {
      if (httpStatus(err) !== 404) throw err;
      const row = await registerRepo(vars.target, "", forge);
      if (!vars.credentialId) return { existed: false, row, bound: false, forbidden: false };
      try {
        await setRepoCredentials(vars.target, [
          { credential_id: vars.credentialId, role: "forge" },
        ]);
        return { existed: false, row, bound: true, forbidden: false };
      } catch (bindErr) {
        // The repo row is already stored. 403 is an operator without admin
        // scope: keep the registration and say the bind did not land.
        return { existed: false, row, bound: false, forbidden: httpStatus(bindErr) === 403 };
      }
    }
    toast.error(
      `${vars.target} is already registered; its account bindings were left unchanged`,
    );
    return { existed: true };
  }

  function reportBinding(bound: boolean, forbidden: boolean, target: string) {
    if (forbidden) toast.error(BIND_FORBIDDEN);
    if (bound) {
      void queryClient.invalidateQueries({ queryKey: ["credentials", "repo", target] });
    }
  }

  const register = useMutation({
    mutationFn: registerAndBind,
    onSuccess: (outcome, { target }) => {
      if (outcome.existed) return;
      toast.success(`${outcome.row.repo} registered`);
      reportBinding(outcome.bound, outcome.forbidden, target);
      setSelected("");
      setManualRepo("");
      void queryClient.invalidateQueries({ queryKey: ["repos"] });
      void queryClient.invalidateQueries({ queryKey: ["github", "discoverable-repos"] });
      void queryClient.invalidateQueries({ queryKey: ["gitlab", "discoverable-projects"] });
      void queryClient.invalidateQueries({ queryKey: ["qa-dashboard"] });
    },
    onError: () => toast.error("Failed to register repo — expected a path like owner/name"),
  });

  const registerAndOnboard = useMutation({
    mutationFn: registerAndBind,
    onSuccess: (outcome, { target }) => {
      if (outcome.existed) return;
      reportBinding(outcome.bound, outcome.forbidden, target);
      void queryClient.invalidateQueries({ queryKey: ["repos"] });
      void queryClient.invalidateQueries({ queryKey: ["github", "discoverable-repos"] });
      void queryClient.invalidateQueries({ queryKey: ["gitlab", "discoverable-projects"] });
      void navigate(`/repos/onboard?repo=${encodeURIComponent(outcome.row.repo)}`);
    },
    onError: () => toast.error("Failed to register repo — expected a path like owner/name"),
  });

  const refresh = async () => {
    try {
      if (forge === "gitlab") {
        const data = await fetchDiscoverableProjects(true);
        queryClient.setQueryData(["gitlab", "discoverable-projects"], data);
      } else {
        const data = await fetchDiscoverableRepos(true);
        queryClient.setQueryData(["github", "discoverable-repos"], data);
      }
    } catch {
      toast.error("Could not refresh the discoverable repo list");
    }
  };

  const activeDiscovery = forge === "gitlab" ? gitlabDiscovery : discovery;
  const busy = register.isPending || registerAndOnboard.isPending;
  const truncated = discovery.data?.truncated_env_keys ?? [];
  const failed =
    (forge === "gitlab" ? gitlabDiscovery.data?.failed : discovery.data?.failed) ?? [];

  return (
    <div className="space-y-3 rounded-lg border border-white/10 bg-white/[0.02] p-4" data-testid="add-repo-panel">
      <div className="flex items-center justify-between">
        <h2 className="text-section font-semibold">Add repo</h2>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => void refresh()}
          disabled={activeDiscovery.isLoading}
          data-testid="add-repo-refresh"
          aria-label="Refresh discoverable repos"
        >
          <RefreshCw className="h-4 w-4" />
        </Button>
      </div>
      {gitlabConfigured && (
        <div className="flex items-center gap-2" data-testid="add-repo-forge-toggle">
          <span className="text-sm text-white/60">Forge</span>
          {(["github", "gitlab"] as Forge[]).map((f) => (
            <Button
              key={f}
              type="button"
              size="sm"
              variant={forge === f ? "secondary" : "ghost"}
              onClick={() => {
                setForge(f);
                setSelected("");
              }}
              data-testid={`add-repo-forge-${f}`}
            >
              {f === "github" ? "GitHub" : "GitLab"}
            </Button>
          ))}
        </div>
      )}
      <p className="text-sm text-white/60">
        {forge === "gitlab"
          ? "Projects the configured GitLab token can reach, minus the ones already registered. Nested subgroups are supported."
          : "Repos the configured GitHub tokens can reach, minus the ones already registered. Each entry names the granting account, or the token env key when the row came from the environment — values never leave the orchestrator."}
      </p>

      {!manualMode ? (
        <Select
          value={selected}
          onChange={(e) => setSelected(e.target.value)}
          aria-label="Discoverable repository"
          data-testid="add-repo-select"
        >
          <option value="">
            {activeDiscovery.isLoading
              ? "Discovering repositories…"
              : activeDiscovery.isError
                ? `Discovery failed — check ${forge === "gitlab" ? "GITLAB_TOKEN" : "GITHUB_TOKEN"} configuration`
                : "Select a repository…"}
          </option>
          {grouped.map(([owner, entries]) => (
            <optgroup key={owner} label={owner}>
              {entries.map((e) => (
                <option key={e.full_name} value={e.full_name}>
                  {e.label}
                </option>
              ))}
            </optgroup>
          ))}
        </Select>
      ) : (
        <div className="flex items-center gap-2">
          <Input
            value={manualRepo}
            onChange={(e) => setManualRepo(e.target.value)}
            placeholder={forge === "gitlab" ? "group/subgroup/project" : "owner/name"}
            className="max-w-xs"
            data-testid="add-repo-manual-input"
          />
          <Badge tone="warning">Unverified — not checked against any token</Badge>
        </div>
      )}

      {forge === "github" && selectedEntry?.push === false && !manualMode && (
        <p className="text-sm text-amber-400" data-testid="add-repo-push-advisory">
          GitHub reports no push access for this token's user (advisory — fine-grained PAT grants
          differ). Onboarding can clone and analyze, but PRs may fail.
        </p>
      )}

      {forge === "github" && truncated.length > 0 && (
        <p className="text-sm text-white/60" data-testid="add-repo-truncation-notice">
          Listing truncated for {truncated.join(", ")} — repos beyond the first 500 per token are
          not shown. Use manual entry for anything missing.
        </p>
      )}

      {failed.length > 0 && (
        <p className="text-sm text-amber-400" data-testid="add-repo-failed-credentials">
          Could not list repos with: {failed.join(", ")}
        </p>
      )}

      <div className="flex items-center gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={!repoValid || busy}
          onClick={() =>
            register.mutate({ target: repo, credentialId: credentialIdFor(repo) })
          }
          data-testid="add-repo-register"
        >
          {register.isPending ? "Registering…" : "Register"}
        </Button>
        <Button
          type="button"
          size="sm"
          disabled={!repoValid || busy}
          onClick={() =>
            registerAndOnboard.mutate({ target: repo, credentialId: credentialIdFor(repo) })
          }
          data-testid="add-repo-register-onboard"
        >
          {registerAndOnboard.isPending ? "Registering…" : "Register & onboard"}
        </Button>
        <button
          type="button"
          className="text-sm text-white/60 hover:underline"
          onClick={() => setManualMode((m) => !m)}
          data-testid="add-repo-manual-toggle"
        >
          {manualMode ? "Pick from discovered repos" : "Manual entry"}
        </button>
      </div>
    </div>
  );
}
