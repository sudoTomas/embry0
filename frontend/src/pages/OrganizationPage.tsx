import { Link, useParams } from "react-router";
import { Building2, ClipboardList, FolderGit2, Radar, Wrench } from "lucide-react";
import { formatErrorDetail } from "@/api/errorDetail";
import { AccessChips } from "@/components/orgs/AccessChips";
import { DiscoveryStatus } from "@/components/orgs/DiscoveryStatus";
import { OrgActPanel } from "@/components/orgs/OrgActPanel";
import { OrgNeedsList } from "@/components/orgs/OrgNeedsList";
import { OrgReposTable } from "@/components/orgs/OrgReposTable";
import { PageError } from "@/components/PageError";
import { Badge } from "@/components/ui/Badge";
import { Button, buttonVariants } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { DashboardSkeleton, Skeleton } from "@/components/ui/PageSkeleton";
import { useCredentials } from "@/hooks/useCredentials";
import {
  useDiscoverOrganization,
  useOrganization,
  useOrganizationNeeds,
  useOrganizationRepos,
  useOrganizationRuns,
} from "@/hooks/useOrgs";
import { ORG_WIZARD_PATH, credentialLabel } from "@/lib/orgs";
import { useAuthStore } from "@/stores/authStore";
import { cn, formatDate } from "@/lib/utils";
import type { ReactNode } from "react";

function httpStatus(err: unknown): number | undefined {
  return (err as { response?: { status?: number } } | null)?.response?.status;
}

function Section({ icon: Icon, title, children }: { icon: typeof Building2; title: string; children: ReactNode }) {
  return (
    <section className="min-w-0 space-y-2">
      <div className="flex items-center gap-2">
        <Icon className="h-4 w-4 text-white/50" />
        <h2 className="text-lg font-semibold">{title}</h2>
      </div>
      {children}
    </section>
  );
}

/** One organization: access, the latest discovery, its repositories, what embry0 needs, and acting on it. */
export function OrganizationPage() {
  const owner = (useParams().owner ?? "").toLowerCase();
  const authMode = useAuthStore((s) => s.authMode);
  const userTier = useAuthStore((s) => s.user?.tier);
  const isAdmin = authMode === "key" || userTier === "admin";

  const org = useOrganization(owner);
  const found = org.isSuccess;
  const runs = useOrganizationRuns(owner, found);
  const repos = useOrganizationRepos(owner, found);
  const needs = useOrganizationNeeds(owner, found);
  const credentials = useCredentials();
  const discover = useDiscoverOrganization(owner);

  if (org.isError) {
    if (httpStatus(org.error) === 404) {
      return (
        <EmptyState
          icon={Building2}
          title={`No organization ${owner}`}
          description="It is not registered in embry0. Add it to see its repositories and what they need."
        >
          <Link to={ORG_WIZARD_PATH} className={cn(buttonVariants({ size: "sm" }))}>
            Add organization
          </Link>
        </EmptyState>
      );
    }
    return <PageError message={`Failed to load ${owner}`} onRetry={() => void org.refetch()} />;
  }
  if (org.isLoading || !org.data) return <DashboardSkeleton />;

  const o = org.data;
  const latest = runs.data?.[0] ?? null;
  const running = latest?.status === "running";
  const discoverError = discover.error
    ? httpStatus(discover.error) === 403
      ? "Discovery requires admin scope."
      : (formatErrorDetail(
          (discover.error as { response?: { data?: { detail?: unknown } } }).response?.data?.detail,
        ) ?? "Discovery did not start.")
    : null;

  return (
    <div className="min-w-0 space-y-6" data-testid="org-page">
      <div className="space-y-2">
        <Link to="/orgs" className="text-sm text-white/50 hover:underline">
          ← Organizations
        </Link>
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="title-pixel text-2xl font-bold">{o.display_name || o.owner}</h1>
          <Badge tone="neutral">{o.forge === "gitlab" ? "GitLab" : "GitHub"}</Badge>
          <Badge tone={o.status === "active" ? "success" : o.status === "paused" ? "warning" : "neutral"}>
            {o.status}
          </Badge>
          <AccessChips access={o.access} />
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="ml-auto"
            onClick={() => discover.mutate()}
            disabled={!isAdmin || running || discover.isPending || o.status !== "active"}
            data-testid="org-rediscover"
          >
            <Radar className="h-4 w-4" /> Rediscover
          </Button>
        </div>
        <p className="text-sm text-white/60">
          {credentialLabel(o, credentials.data)}
          {o.repo_count !== null && ` · ${o.repo_count} repositories`}
          {` · discovered ${o.discovered_at ? formatDate(o.discovered_at) : "never"}`}
        </p>
        {typeof o.access.detail === "string" && o.access.detail && (
          <p className="text-xs text-muted-foreground">{o.access.detail}</p>
        )}
        {discoverError && (
          <p className="text-sm text-destructive" role="alert" data-testid="org-discover-error">
            {discoverError}
          </p>
        )}
      </div>

      <Section icon={Radar} title="Latest discovery">
        {runs.isLoading ? <Skeleton className="h-12 w-full" /> : <DiscoveryStatus owner={o.owner} run={latest} />}
      </Section>

      <Section icon={ClipboardList} title="What embry0 needs">
        <OrgNeedsList
          needs={needs.data}
          isLoading={needs.isLoading}
          isError={needs.isError}
          onRetry={() => void needs.refetch()}
        />
      </Section>

      <Section icon={Wrench} title="Act">
        {needs.isError ? (
          <p className="text-sm text-muted-foreground">The needs list did not load, so there is nothing to act on.</p>
        ) : (
          <OrgActPanel owner={o.owner} needs={needs.data} isAdmin={isAdmin} />
        )}
      </Section>

      <Section icon={FolderGit2} title="Repositories">
        {repos.isError ? (
          <PageError message="Failed to load the repositories" onRetry={() => void repos.refetch()} />
        ) : repos.data ? (
          <OrgReposTable repos={repos.data} />
        ) : (
          <Skeleton className="h-32 w-full" />
        )}
      </Section>
    </div>
  );
}
