import { useState } from "react";
import { Link } from "react-router";
import { FileCode2 } from "lucide-react";
import { useRegisteredRepos } from "@/hooks/useRepos";
import { AddRepoPanel } from "@/components/repos/AddRepoPanel";
import { PageError } from "@/components/PageError";
import { DashboardSkeleton } from "@/components/ui/PageSkeleton";
import type { Forge } from "@/api/repos";
import { Badge } from "@/components/ui/Badge";
import { Input } from "@/components/ui/Input";
import { providerLabel } from "@/lib/status";

// RAV-1301: the central repo registry page. Adding a repo starts here (the
// token-discovered dropdown in AddRepoPanel); the registry list below shows
// every registered repo with its config/environment entry points. The QA
// page keeps run cards only and links here.

/** RAV-1251 registry row (moved from QaReposPage in RAV-1301): one row of the
 * registered-repos table. Below sm the forge column steps aside, the name
 * truncates and the two links stack, so the testing pill stays on screen. */
function RegisteredRepoRow({
  repo,
  hasQaConfig,
  forge,
  dockerAccess,
}: {
  repo: string;
  hasQaConfig: boolean;
  forge: Forge;
  dockerAccess: boolean;
}) {
  const encoded = encodeURIComponent(repo);
  return (
    <tr data-testid="registered-repo-row">
      <td className="w-full max-w-0 sm:max-w-none">
        <div className="flex min-w-0 items-center gap-2">
          <Link to={`/repos/${repo}`} className="truncate font-medium hover:text-primary hover:underline" title={repo}>
            {repo}
          </Link>
          {/* #522: an admin grant that hands this repo's code the shared Docker daemon. */}
          {dockerAccess && (
            <Badge
              tone="warning"
              title="Code from this repo can control the shared Docker daemon, including the proxies holding your tokens."
            >
              Docker access
            </Badge>
          )}
        </div>
      </td>
      {/* #169: which forge this repo lives on decides clone URL, API base and
          credential proxy — surface it rather than leaving operators to infer
          it from the path shape. */}
      <td className="hidden sm:table-cell">
        <Badge tone="neutral" data-testid="repo-forge-badge">
          {providerLabel(forge)}
        </Badge>
      </td>
      <td>
        {hasQaConfig ? (
          <Badge tone="success">Test setup saved</Badge>
        ) : (
          <Badge tone="warning">Testing not set up</Badge>
        )}
      </td>
      <td>
        <div className="flex flex-col items-end gap-0.5 whitespace-nowrap text-sm sm:flex-row sm:items-center sm:justify-end sm:gap-2">
          <Link to={`/repos/${repo}`} className="text-fg-secondary hover:text-primary hover:underline">
            Setup
          </Link>
          <span className="hidden text-white/20 sm:inline" aria-hidden="true">·</span>
          <Link to={`/environments?repo=${encoded}`} className="text-fg-secondary hover:text-primary hover:underline">
            Environment
          </Link>
        </div>
      </td>
    </tr>
  );
}

export function ReposPage() {
  const registry = useRegisteredRepos({ refetchInterval: 30_000 });
  const [query, setQuery] = useState("");

  if (registry.isError) {
    return <PageError message="Failed to load the repo registry" onRetry={() => registry.refetch()} />;
  }
  if (registry.isLoading || !registry.data) return <DashboardSkeleton />;

  const needle = query.trim().toLowerCase();
  const shown = needle ? registry.data.filter((r) => r.repo.toLowerCase().includes(needle)) : registry.data;

  return (
    <div className="space-y-4">
      <h1 className="title-pixel text-xl font-bold">Repos</h1>

      <p className="text-sm text-white/60" data-testid="repos-orgs-pointer">
        Looking after every repository under one owner?{" "}
        <Link to="/orgs" className="text-fg hover:text-primary hover:underline">
          Add it as an organization
        </Link>
        .
      </p>

      <AddRepoPanel />

      <div className="space-y-3 pt-2">
        <div className="flex items-center gap-2">
          <FileCode2 className="h-4 w-4 text-fg-muted" />
          <h2 className="text-section font-semibold">Registered repos</h2>
        </div>
        <p className="text-sm text-white/60">
          Every repository embry0 works on. Its test setup and settings are kept in embry0, not in
          the repository.
        </p>
        {registry.data.length === 0 ? (
          <p className="text-sm text-white/60" data-testid="registry-empty">
            No repos registered yet — pick one from the dropdown above.
          </p>
        ) : (
          <>
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Find a repository"
              aria-label="Find a repository"
              className="max-w-xs"
              data-testid="registry-filter"
            />
            {shown.length === 0 ? (
              <p className="text-sm text-white/60" data-testid="registry-no-match">
                {`No repository matches "${query.trim()}".`}
              </p>
            ) : (
              <div className="overflow-x-auto rounded-lg border border-border" data-testid="registered-repos-list">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th scope="col">Repository</th>
                      <th scope="col" className="hidden sm:table-cell">Forge</th>
                      <th scope="col">Testing</th>
                      <th scope="col" className="text-right">Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((r) => (
                      <RegisteredRepoRow
                        key={r.repo}
                        repo={r.repo}
                        hasQaConfig={r.has_qa_config}
                        forge={r.forge}
                        dockerAccess={r.docker_access === true}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
