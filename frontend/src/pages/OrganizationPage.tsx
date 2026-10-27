import { Link, useParams, useSearchParams } from "react-router";
import { Building2, ClipboardList, FolderGit2, Radar, Ruler, ShieldCheck, Wrench } from "lucide-react";
import { formatErrorDetail } from "@/api/errorDetail";
import { toastApiError } from "@/api/errorToast";
import { AdminButton } from "@/components/auth/AdminButton";
import { AccessChips } from "@/components/orgs/AccessChips";
import { ContextAuditSetting } from "@/components/orgs/ContextAuditSetting";
import { ContextAuditTab } from "@/components/orgs/ContextAuditTab";
import { DiscoveryStatus } from "@/components/orgs/DiscoveryStatus";
import { OrgActPanel } from "@/components/orgs/OrgActPanel";
import { OrgCredential } from "@/components/orgs/OrgCredential";
import { OrgNeedsList } from "@/components/orgs/OrgNeedsList";
import { OrgReposTable } from "@/components/orgs/OrgReposTable";
import { OrgSettingsTab } from "@/components/orgs/OrgSettingsTab";
import { RediscoverSetting } from "@/components/orgs/RediscoverSetting";
import { OrgStandardSummary } from "@/components/standards/OrgStandardSummary";
import { PageError } from "@/components/PageError";
import { Badge } from "@/components/ui/Badge";
import { Button, buttonVariants } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { DashboardSkeleton, Skeleton } from "@/components/ui/PageSkeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/Tabs";
import { useCredentials } from "@/hooks/useCredentials";
import { hasAnswered, useFirstPaint } from "@/hooks/useFirstPaint";
import {
  useDiscoverOrganization,
  useOrganization,
  useOrganizationNeeds,
  useOrganizationRepos,
  useOrganizationRuns,
  useReprobeOrganization,
} from "@/hooks/useOrgs";
import { useOwnerStandards } from "@/hooks/useStandards";
import { ORG_WIZARD_PATH, contextAuditDays } from "@/lib/orgs";
import { ORG_TABS, orgTab } from "@/lib/orgTabs";
import { useIsAdmin } from "@/stores/authStore";
import { cn, formatDate } from "@/lib/utils";
import { statusLabel } from "@/lib/status";
import type { ReactNode } from "react";

function httpStatus(err: unknown): number | undefined {
  return (err as { response?: { status?: number } } | null)?.response?.status;
}

function Section({ icon: Icon, title, children }: { icon: typeof Building2; title: string; children: ReactNode }) {
  return (
    <section className="min-w-0 space-y-2">
      <div className="flex items-center gap-2">
        <Icon className="h-4 w-4 text-white/50" />
        <h2 className="text-section font-semibold">{title}</h2>
      </div>
      {children}
    </section>
  );
}

/** One organization: access, the latest discovery, its repositories, what embry0 needs, and acting on it. */
export function OrganizationPage() {
  const owner = (useParams().owner ?? "").toLowerCase();
  const isAdmin = useIsAdmin();

  const org = useOrganization(owner);
  const found = org.isSuccess;
  const runs = useOrganizationRuns(owner, found);
  const repos = useOrganizationRepos(owner, found);
  const needs = useOrganizationNeeds(owner, found);
  const standards = useOwnerStandards(owner, found);
  const credentials = useCredentials();
  const discover = useDiscoverOrganization(owner);
  const reprobe = useReprobeOrganization(owner);
  // The open tab lives in the URL so a setting can be linked to.
  const [params, setParams] = useSearchParams();
  const tab = orgTab(params.get("tab")).value;
  const openTab = (value: string) => setParams(value === ORG_TABS[0].value ? {} : { tab: value }, { replace: true });
  // The page paints once the latest discovery, the needs and the standard
  // have first answered. Drawn before, each section grew from its skeleton
  // as its answer landed and pushed every section under it down (a layout
  // shift of 0.12 to 0.42 on a phone, 0.18 to 0.35 at desktop width). The
  // standard is read here so the page can wait on it; its summary shares
  // the request. The repositories are not waited on: their table is last,
  // so its arrival moves nothing.
  const painted = useFirstPaint(hasAnswered(runs) && hasAnswered(needs) && hasAnswered(standards));

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
  if (org.isLoading || !org.data || !painted) return <DashboardSkeleton />;

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
          <h1 className="title-page text-xl font-bold">{o.display_name || o.owner}</h1>
          <Badge tone="neutral">{o.forge === "gitlab" ? "GitLab" : "GitHub"}</Badge>
          <Badge tone={o.status === "active" ? "success" : o.status === "paused" ? "warning" : "neutral"}>
            {statusLabel(o.status)}
          </Badge>
          <AccessChips access={o.access} />
          <AdminButton
            type="button"
            size="sm"
            variant="outline"
            className="ml-auto"
            onClick={() => reprobe.mutate(undefined, { onError: (e) => toastApiError(e, "The probe did not run") })}
            disabled={reprobe.isPending}
            data-testid="org-reprobe"
          >
            <ShieldCheck className="h-4 w-4" /> {reprobe.isPending ? "Probing…" : "Probe again"}
          </AdminButton>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => discover.mutate()}
            disabled={!isAdmin || running || discover.isPending || o.status !== "active"}
            data-testid="org-rediscover"
          >
            <Radar className="h-4 w-4" /> Rediscover
          </Button>
        </div>
        <p className="text-sm text-white/60" data-testid="org-meta">
          <OrgCredential org={o} credentials={credentials.data} />
          {o.repo_count !== null && ` · ${o.repo_count} repositories`}
          {` · discovered ${o.discovered_at ? formatDate(o.discovered_at) : "never"}`}
        </p>
        <RediscoverSetting org={o} isAdmin={isAdmin} />
        <ContextAuditSetting org={o} isAdmin={isAdmin} />
        {typeof o.access.detail === "string" && o.access.detail && (
          <p className="text-xs text-muted-foreground">{o.access.detail}</p>
        )}
        {discoverError && (
          <p className="text-sm text-destructive" role="alert" data-testid="org-discover-error">
            {discoverError}
          </p>
        )}
      </div>

      <Tabs defaultValue={ORG_TABS[0].value} value={tab} onValueChange={openTab}>
        <TabsList>
          {ORG_TABS.map((t) => (
            <TabsTrigger key={t.value} value={t.value}>
              {t.label}
            </TabsTrigger>
          ))}
        </TabsList>
        <TabsContent value="overview" className="mt-6 space-y-6">
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

          <Section icon={Ruler} title="Design standard">
            <OrgStandardSummary owner={o.owner} />
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
        </TabsContent>
        <TabsContent value="context-audit" className="mt-6">
          <ContextAuditTab owner={o.owner} days={contextAuditDays(o)} />
        </TabsContent>
        <TabsContent value="settings" className="mt-6">
          <OrgSettingsTab org={o} isAdmin={isAdmin} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
