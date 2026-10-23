import type { ReactNode } from "react";
import { Link, useParams } from "react-router";
import { BookOpen, FolderGit2, History, ListChecks, Palette, Ruler, Type } from "lucide-react";
import { standardApiPath } from "@/api/standards";
import { PageError } from "@/components/PageError";
import { StandardReposTable } from "@/components/standards/StandardReposTable";
import {
  ColourRoles,
  OtherRoles,
  RulesList,
  TypeScaleTable,
  VersionsTable,
} from "@/components/standards/StandardParts";
import { Badge } from "@/components/ui/Badge";
import { DashboardSkeleton, Skeleton } from "@/components/ui/PageSkeleton";
import { hasAnswered, useFirstPaint } from "@/hooks/useFirstPaint";
import { useOrganizations } from "@/hooks/useOrgs";
import { useRegisteredRepos } from "@/hooks/useRepos";
import { useAuditJobStatuses, useOwnerStandards, useStandardVersions } from "@/hooks/useStandards";
import { colourRoles, otherRoles, standardRows, standardTitle } from "@/lib/standards";
import { statusLabel, statusTone } from "@/lib/status";
import { formatDate } from "@/lib/utils";
import { useAuthStore, useIsAdmin } from "@/stores/authStore";

/** The one kind of standard this release holds. */
const KIND = "design";

function Section({
  icon: Icon,
  title,
  children,
  testId,
}: {
  icon: typeof Palette;
  title: string;
  children: ReactNode;
  testId?: string;
}) {
  return (
    <section className="min-w-0 space-y-2" data-testid={testId}>
      <div className="flex items-center gap-2">
        <Icon className="h-4 w-4 text-fg-muted" />
        <h2 className="text-section font-semibold">{title}</h2>
      </div>
      {children}
    </section>
  );
}

/**
 * An owner's design standard: the repositories held to it, furthest first,
 * with their modes and audits; then the standard itself (colour roles, other
 * roles, type scale, rules) and its versions. The JSON is written through
 * the API, so the page names the path instead of offering an editor.
 */
export function StandardsPage() {
  const owner = (useParams().owner ?? "").toLowerCase();
  const userTier = useAuthStore((s) => s.user?.tier);
  const keyTier = useAuthStore((s) => s.keyTier);
  const isAdmin = useIsAdmin();
  const canAudit = isAdmin || userTier === "operator" || keyTier === "operator";

  // From the list, not GET /orgs/<owner>: a standard can exist before its
  // owner is registered, and that read would answer 404 on every load.
  const orgs = useOrganizations();
  const org = orgs.data?.find((o) => o.owner.toLowerCase() === owner) ?? null;
  const standards = useOwnerStandards(owner);
  const registered = useRegisteredRepos();
  const active = standards.data?.standards.find((s) => s.kind === KIND) ?? null;
  const versions = useStandardVersions(owner, KIND, standards.isSuccess);

  const rows = standards.data ? standardRows(owner, KIND, registered.data ?? [], standards.data.repos) : [];
  const jobIds = rows.flatMap((r) => (r.state.mode !== "off" && r.state.last_audit_job_id ? [r.state.last_audit_job_id] : []));
  const jobStatuses = useAuditJobStatuses(owner, jobIds);
  // The page paints once the standards, the registry and the organizations
  // list have first answered; the three are read side by side. Painted on
  // the standards alone, it changed as the other two landed a moment later:
  // the repositories' skeleton gave way to their table and the back link
  // replaced its spacer, pushing the standard's parts down (a layout shift
  // of up to 0.11 on a phone). The versions are not waited on: they sit at
  // the foot of the page.
  const painted = useFirstPaint(hasAnswered(standards) && hasAnswered(registered) && hasAnswered(orgs));

  if (standards.isError) {
    return <PageError message={`Failed to load the standards of ${owner}`} onRetry={() => void standards.refetch()} />;
  }
  if (standards.isLoading || !standards.data || !painted) return <DashboardSkeleton />;

  const putPath = standardApiPath(owner, KIND);

  return (
    <div className="min-w-0 space-y-6" data-testid="standards-page">
      <div className="space-y-2">
        {orgs.isPending ? (
          <span className="block h-5" aria-hidden="true" />
        ) : (
          // Back to the organization when it is registered; else to the list, not to a page that does not exist.
          <Link to={org ? `/orgs/${owner}` : "/orgs"} className="text-sm text-fg-muted hover:underline" data-testid="standard-back">
            {org ? `← ${org.display_name || owner}` : "← Organizations"}
          </Link>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <h1 className="title-pixel text-xl font-bold">{standardTitle(KIND)}</h1>
          {active && (
            <>
              <Badge tone="neutral">{`Version ${active.version}`}</Badge>
              <Badge tone={statusTone(active.status)}>{statusLabel(active.status)}</Badge>
            </>
          )}
        </div>
        {active ? (
          <>
            <p className="text-sm text-fg-muted">
              {`${owner} · written ${active.created_at ? formatDate(active.created_at) : "at an unknown time"}`}
              {active.created_by ? ` by ${active.created_by}` : ""}
            </p>
            {active.prose && (
              <p className="max-w-3xl whitespace-pre-line text-sm text-fg-secondary" data-testid="standard-prose">
                {active.prose}
              </p>
            )}
            {active.references.length > 0 && (
              <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-fg-muted" data-testid="standard-references">
                <span>References</span>
                {active.references.map((ref) =>
                  /^https?:\/\//.test(ref) ? (
                    <a key={ref} href={ref} target="_blank" rel="noreferrer" className="font-mono text-fg-secondary hover:text-primary hover:underline">
                      {ref.replace(/^https?:\/\//, "")}
                    </a>
                  ) : (
                    <span key={ref} className="font-mono text-fg-secondary">
                      {ref}
                    </span>
                  ),
                )}
              </p>
            )}
          </>
        ) : (
          <p
            className="max-w-3xl rounded-lg border border-border bg-surface px-3 py-2 text-sm text-fg-secondary"
            data-testid="standard-none"
          >
            {`No ${KIND} standard for ${owner} yet. An admin writes the first version with `}
            <code className="font-mono text-xs">PUT {putPath}</code>
            {". Repositories can be put in a mode before it exists; none can be audited until it does."}
          </p>
        )}
      </div>

      <Section icon={FolderGit2} title="Repositories" testId="standard-repos-section">
        {registered.isLoading ? (
          <Skeleton className="h-32 w-full" />
        ) : (
          <StandardReposTable
            owner={owner}
            kind={KIND}
            rows={rows}
            hasStandard={active !== null}
            isAdmin={isAdmin}
            canAudit={canAudit}
            jobStatuses={jobStatuses}
          />
        )}
        {registered.isError && (
          <p className="text-xs text-warning">The registry did not load, so only repositories with a stored mode are listed.</p>
        )}
      </Section>

      {active && (
        <>
          <Section icon={Palette} title="Colour roles" testId="standard-colours-section">
            <ColourRoles roles={colourRoles(active)} />
          </Section>

          <div className="grid min-w-0 gap-6 xl:grid-cols-2">
            <Section icon={Type} title="Type scale">
              <TypeScaleTable standard={active} />
            </Section>
            <Section icon={Ruler} title="Other roles">
              <OtherRoles roles={otherRoles(active)} />
            </Section>
          </div>

          <Section icon={ListChecks} title="Rules">
            <RulesList rules={active.schema.rules} />
          </Section>
        </>
      )}

      <Section icon={History} title="Versions">
        {versions.isError ? (
          <p className="text-sm text-destructive">The versions did not load.</p>
        ) : versions.data ? (
          <VersionsTable versions={versions.data} />
        ) : (
          <Skeleton className="h-12 w-full" />
        )}
        <p className="flex flex-wrap items-center gap-2 text-xs text-fg-muted" data-testid="standard-put-path">
          <BookOpen className="h-3.5 w-3.5" aria-hidden="true" />
          A new version is written by an admin through the API, the whole version in one body:
          <code className="rounded bg-overlay px-1.5 py-0.5 font-mono text-fg-secondary">PUT {putPath}</code>
        </p>
      </Section>
    </div>
  );
}
