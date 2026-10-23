import { Link } from "react-router";
import type { RepoStandardState } from "@/api/standards";
import { Badge } from "@/components/ui/Badge";
import { useAuditJobStatuses } from "@/hooks/useStandards";
import { modeLabel, ownerOf, scoreTextClass, standardTitle } from "@/lib/standards";
import { statusLabel, statusTone } from "@/lib/status";
import { cn, formatDate } from "@/lib/utils";
import { Fact, FactStrip, QUIET_LINK } from "./FactStrip";

/**
 * One line per kind of standard on a repository's page: its mode, the score
 * and findings of its newest completed audit, and the newest audit job. A
 * server older than standards sends no `standards`, and the row is left out.
 */
export function RepoStandardsRow({ repo, standards }: { repo: string; standards: readonly RepoStandardState[] | undefined }) {
  const owner = ownerOf(repo);
  const jobIds = (standards ?? []).flatMap((s) => (s.last_audit_job_id ? [s.last_audit_job_id] : []));
  const statuses = useAuditJobStatuses(owner, jobIds);
  if (!standards || standards.length === 0) return null;

  return (
    <div className="space-y-1.5" data-testid="repo-standards">
      {standards.map((s) => {
        const audited = s.last_audit_at !== null;
        const jobStatus = s.last_audit_job_id ? statuses.get(s.last_audit_job_id) : undefined;
        return (
          <FactStrip key={s.kind} data-testid="repo-standard-row">
            <Link to={`/orgs/${owner}/standards`} className={cn(QUIET_LINK, "font-medium text-fg")} data-testid="repo-standard-link">
              {standardTitle(s.kind)}
            </Link>
            <Fact label="Mode" data-testid="repo-standard-mode">
              {modeLabel(s.mode)}
            </Fact>
            <Fact label="Score" data-testid="repo-standard-score">
              {s.score === null ? (
                <span className="text-fg-muted">none yet</span>
              ) : (
                <span className={cn("font-mono font-semibold", scoreTextClass(s.score))}>{s.score}</span>
              )}
            </Fact>
            <Fact label="Findings" data-testid="repo-standard-findings">
              {audited ? <span className="font-mono">{s.findings_open ?? 0}</span> : <span className="text-fg-muted">-</span>}
            </Fact>
            <Fact label="Last audit">{s.last_audit_at ? formatDate(s.last_audit_at) : "never"}</Fact>
            {s.last_audit_job_id && (
              <Link
                to={`/jobs/${s.last_audit_job_id}`}
                className="inline-flex items-center gap-1.5 text-xs text-fg-secondary hover:text-primary hover:underline"
                data-testid="repo-standard-job"
              >
                Audit job
                {jobStatus && <Badge tone={statusTone(jobStatus)}>{statusLabel(jobStatus)}</Badge>}
              </Link>
            )}
          </FactStrip>
        );
      })}
    </div>
  );
}
