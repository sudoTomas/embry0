import { Link } from "react-router";
import { FileCode2, Plus } from "lucide-react";
import { useRegisteredRepos } from "@/hooks/useRepos";
import { AddRepoPanel } from "@/components/repos/AddRepoPanel";
import { PageError } from "@/components/PageError";
import { DashboardSkeleton } from "@/components/ui/PageSkeleton";
import type { Forge } from "@/api/repos";
import { Badge } from "@/components/ui/Badge";
import { buttonVariants } from "@/components/ui/Button";
import { cn } from "@/lib/utils";

// RAV-1301: the central repo registry page. Adding a repo starts here (the
// token-discovered dropdown in AddRepoPanel); the registry list below shows
// every registered repo with its config/environment entry points. The QA
// page keeps run cards only and links here.

/** RAV-1251 registry row (moved from QaReposPage in RAV-1301). */
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
    <div
      className="flex items-center justify-between rounded-lg border border-white/10 bg-white/[0.02] px-4 py-3"
      data-testid="registered-repo-row"
    >
      <div className="flex items-center gap-3">
        <Link to={`/repos/${repo}`} className="font-medium hover:underline">
          {repo}
        </Link>
        {/* #169: which forge this repo lives on decides clone URL, API base
            and credential proxy — surface it rather than leaving operators to
            infer it from the path shape. */}
        <Badge tone="neutral" data-testid="repo-forge-badge">
          {forge === "gitlab" ? "GitLab" : "GitHub"}
        </Badge>
        {hasQaConfig ? (
          <Badge tone="success">config stored</Badge>
        ) : (
          <Badge tone="warning">no QA config</Badge>
        )}
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
      <div className="flex items-center gap-2 text-sm">
        <Link to={`/qa/repos/${encoded}/config`} className="text-white/60 hover:underline">
          Config
        </Link>
        <span className="text-white/20">·</span>
        <Link to={`/environments?repo=${encoded}`} className="text-white/60 hover:underline">
          Environment
        </Link>
      </div>
    </div>
  );
}

export function ReposPage() {
  const registry = useRegisteredRepos({ refetchInterval: 30_000 });

  if (registry.isError) {
    return <PageError message="Failed to load the repo registry" onRetry={() => registry.refetch()} />;
  }
  if (registry.isLoading || !registry.data) return <DashboardSkeleton />;

  return (
    <div className="space-y-4 p-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">Repos</h1>
        <Link
          to="/repos/onboard"
          className={cn(buttonVariants({ variant: "outline", size: "sm" }))}
          data-testid="onboard-repo-cta"
        >
          <Plus className="h-4 w-4" /> Onboard repo
        </Link>
      </div>

      <p className="text-sm text-white/60" data-testid="repos-orgs-pointer">
        Looking after every repository under one owner?{" "}
        <Link to="/orgs" className="text-primary hover:underline">
          Add it as an organization
        </Link>
        .
      </p>

      <AddRepoPanel />

      <div className="space-y-3 pt-2">
        <div className="flex items-center gap-2">
          <FileCode2 className="h-4 w-4 text-white/50" />
          <h2 className="text-lg font-semibold">Registered repos</h2>
        </div>
        <p className="text-sm text-white/60">
          The embry0 repo registry — every configured target repo, with its QA config and
          environment managed here, not in the target repo.
        </p>
        {registry.data.length === 0 ? (
          <p className="text-sm text-white/60" data-testid="registry-empty">
            No repos registered yet — pick one from the dropdown above.
          </p>
        ) : (
          <div className="space-y-2" data-testid="registered-repos-list">
            {registry.data.map((r) => (
              <RegisteredRepoRow
                key={r.repo}
                repo={r.repo}
                hasQaConfig={r.has_qa_config}
                forge={r.forge}
                dockerAccess={r.docker_access === true}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
