import { Link } from "react-router";
import { Building2, Plus } from "lucide-react";
import type { Organization } from "@/api/orgs";
import { AccessChips } from "@/components/orgs/AccessChips";
import { TABLE_BOX, TD, TH } from "@/components/orgs/styles";
import { PageError } from "@/components/PageError";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { buttonVariants } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { useCredentials } from "@/hooks/useCredentials";
import { useOrganizations } from "@/hooks/useOrgs";
import { ORG_WIZARD_PATH, credentialLabel } from "@/lib/orgs";
import { cn, formatDate } from "@/lib/utils";

const STATUS_TONE: Record<Organization["status"], BadgeTone> = {
  active: "success",
  paused: "warning",
  removed: "neutral",
};

function AddButton() {
  return (
    <Link to={ORG_WIZARD_PATH} className={cn(buttonVariants({ size: "sm" }))} data-testid="add-org-cta">
      <Plus className="h-4 w-4" /> Add organization
    </Link>
  );
}

function LoadingRows() {
  return (
    <div className="space-y-2" data-testid="orgs-loading">
      <Skeleton className="h-8 w-48" />
      {Array.from({ length: 4 }).map((_, i) => (
        <Skeleton key={i} className="h-10 w-full" />
      ))}
    </div>
  );
}

/** The forge owners embry0 maintains as a whole (ADR 0010). */
export function OrganizationsPage() {
  const orgs = useOrganizations();
  // Credential labels are a nicety: without them the column shows the id.
  const credentials = useCredentials();

  if (orgs.isError) {
    return <PageError message="Failed to load organizations" onRetry={() => void orgs.refetch()} />;
  }
  if (orgs.isLoading || !orgs.data) return <LoadingRows />;

  return (
    <div className="min-w-0 space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="title-pixel text-2xl font-bold">Organizations</h1>
          <p className="text-sm text-white/60">
            A GitHub organization or user, or a GitLab group, that embry0 looks after as a whole.
          </p>
        </div>
        <AddButton />
      </div>

      {orgs.data.length === 0 ? (
        <EmptyState
          icon={Building2}
          title="No organizations yet"
          description="Add one and embry0 walks every repository under it, then says what it needs to start maintaining each."
          className="rounded-lg border border-white/[0.06]"
        >
          <AddButton />
        </EmptyState>
      ) : (
        <div className={TABLE_BOX}>
          <table className="w-full min-w-[820px] text-sm" data-testid="orgs-table">
            <thead>
              <tr className="border-b border-white/[0.06]">
                <th scope="col" className={TH}>Owner</th>
                <th scope="col" className={TH}>Forge</th>
                <th scope="col" className={TH}>Credential</th>
                <th scope="col" className={TH}>Access</th>
                <th scope="col" className={cn(TH, "text-right")}>Repos</th>
                <th scope="col" className={TH}>Discovered</th>
                <th scope="col" className={TH}>Status</th>
              </tr>
            </thead>
            <tbody>
              {orgs.data.map((org) => (
                <tr key={org.owner} className="border-b border-white/[0.04] last:border-0" data-testid="org-row">
                  <td className={TD}>
                    <Link to={`/orgs/${org.owner}`} className="font-medium hover:underline" data-testid="org-link">
                      {org.owner}
                    </Link>
                    {org.display_name && <div className="text-xs text-muted-foreground">{org.display_name}</div>}
                  </td>
                  <td className={TD}>
                    <Badge tone="neutral">{org.forge === "gitlab" ? "GitLab" : "GitHub"}</Badge>
                  </td>
                  <td className={cn(TD, "text-white/70")}>{credentialLabel(org, credentials.data)}</td>
                  <td className={TD}>
                    <AccessChips access={org.access} />
                  </td>
                  <td className={cn(TD, "text-right font-mono")}>{org.repo_count ?? "-"}</td>
                  <td className={cn(TD, "whitespace-nowrap text-white/60")}>
                    {org.discovered_at ? formatDate(org.discovered_at) : "never"}
                  </td>
                  <td className={TD}>
                    <Badge tone={STATUS_TONE[org.status] ?? "neutral"}>{org.status}</Badge>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
