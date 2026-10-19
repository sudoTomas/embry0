import { Fragment, useMemo, useState } from "react";
import { Link } from "react-router";
import { FolderGit2 } from "lucide-react";
import { STANDARD_MODES, type StandardMode } from "@/api/standards";
import { TABLE_BOX } from "@/components/orgs/styles";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Input } from "@/components/ui/Input";
import { Select } from "@/components/ui/Select";
import { useIsNarrow } from "@/hooks/useIsNarrow";
import { useSetRepoMode, useStartAudit } from "@/hooks/useStandards";
import {
  MODE_HINTS,
  auditBlockedReason,
  isOffAndUnscored,
  modeCounts,
  modeLabel,
  nextAuditRepo,
  requestErrorText,
  scoreTextClass,
  type StandardRepoRow,
} from "@/lib/standards";
import { statusLabel, statusTone } from "@/lib/status";
import { cn, formatDate } from "@/lib/utils";

interface Props {
  owner: string;
  kind: string;
  rows: readonly StandardRepoRow[];
  hasStandard: boolean;
  isAdmin: boolean;
  canAudit: boolean;
  /** Audit job status by job id; a job not in the map has no known status. */
  jobStatuses: ReadonlyMap<string, string>;
}

/** The repository's name under the owner: the page already names the owner. */
function shortName(repo: string): string {
  const slash = repo.indexOf("/");
  return slash === -1 ? repo : repo.slice(slash + 1);
}

/**
 * The owner's registered repositories under one standard, furthest from it
 * first: an admin sets each one's mode, an operator starts its audit. The
 * Audit button of the next repository to audit (under the standard, never
 * audited, free to start) is the view's one gold control; with none, none is.
 * The repositories in mode off that no audit scored, most of an owner's at
 * first, fold behind one row, so the standard below stays within reach; the
 * name filter still searches all of them.
 * On a phone the mode moves under the name, so the score and Audit stay in view.
 */
export function StandardReposTable({ owner, kind, rows, hasStandard, isAdmin, canAudit, jobStatuses }: Props) {
  const setMode = useSetRepoMode(owner, kind);
  const audit = useStartAudit(owner, kind);
  const narrow = useIsNarrow();
  const columns = narrow ? 5 : 6;
  const [text, setText] = useState("");
  const [showOff, setShowOff] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});

  const needle = text.trim().toLowerCase();
  // `rows` is sorted with the off, unscored ones last, so held + folded keeps that order.
  const { held, folded } = useMemo(
    () => ({ held: rows.filter((r) => !isOffAndUnscored(r)), folded: rows.filter(isOffAndUnscored) }),
    [rows],
  );
  const matched = useMemo(
    () => (needle ? rows.filter((r) => r.repo.toLowerCase().includes(needle)) : null),
    [rows, needle],
  );

  const blocked = (row: StandardRepoRow) =>
    auditBlockedReason(row, {
      hasStandard,
      canAudit,
      jobStatus: row.state.last_audit_job_id ? jobStatuses.get(row.state.last_audit_job_id) : undefined,
    });
  const gold = nextAuditRepo(rows, blocked);
  const counts = modeCounts(rows);

  const fail = (repo: string, err: unknown, fallback: string) =>
    setErrors((prev) => ({ ...prev, [repo]: requestErrorText(err, fallback) }));
  const clear = (repo: string) =>
    setErrors((prev) => {
      if (!(repo in prev)) return prev;
      const next = { ...prev };
      delete next[repo];
      return next;
    });

  const changeMode = (repo: string, mode: StandardMode) => {
    clear(repo);
    setMode.mutate({ repo, mode }, { onError: (err) => fail(repo, err, "The mode was not changed.") });
  };
  const startAudit = (repo: string) => {
    clear(repo);
    audit.mutate(repo, { onError: (err) => fail(repo, err, "The audit did not start.") });
  };

  const renderRow = (row: StandardRepoRow) => {
    const { state } = row;
    const jobId = state.last_audit_job_id;
    const jobStatus = jobId ? jobStatuses.get(jobId) : undefined;
    const reason = blocked(row);
    const audited = state.last_audit_at !== null;
    const settingMode = setMode.isPending && setMode.variables?.repo === row.repo;
    const starting = audit.isPending && audit.variables === row.repo;
    const error = errors[row.repo];
    const mode = isAdmin ? (
      <Select
        value={state.mode}
        onChange={(e) => changeMode(row.repo, e.target.value as StandardMode)}
        disabled={settingMode}
        aria-label={`Mode of ${row.repo}`}
        title={MODE_HINTS[state.mode]}
        className={cn(
          // Quiet until pointed at: a box on every one of a long list of rows is noise.
          "h-7 w-[6.5rem] border-0 bg-transparent px-1.5 text-xs hover:bg-overlay [&>option]:bg-overlay",
          state.mode === "off" ? "text-fg-muted" : "text-fg",
        )}
        data-testid="standard-mode-select"
      >
        {STANDARD_MODES.map((m) => (
          <option key={m} value={m}>
            {modeLabel(m)}
          </option>
        ))}
      </Select>
    ) : (
      <span
        className={cn("text-sm", state.mode === "off" ? "text-fg-muted" : "text-fg")}
        title={MODE_HINTS[state.mode]}
        data-testid="standard-mode"
      >
        {modeLabel(state.mode)}
      </span>
    );
    return (
      <Fragment key={row.repo}>
        <tr data-testid="standard-repo-row" data-repo={row.repo}>
          {/* The key column takes the room the others leave and truncates. */}
          <td className="w-full max-w-0 sm:w-auto sm:max-w-none">
            <Link
              to={`/repos/${row.repo}`}
              title={row.repo}
              className="block truncate font-medium hover:underline"
              data-testid="standard-repo-link"
            >
              {shortName(row.repo)}
            </Link>
            {narrow && <div className="-ml-1.5 mt-0.5">{mode}</div>}
          </td>
          {!narrow && <td className="w-px whitespace-nowrap">{mode}</td>}
          <td className="num w-px" data-testid="standard-score">
            {state.score === null ? (
              <span className="text-fg-muted">-</span>
            ) : (
              <span className={cn("font-mono font-semibold", scoreTextClass(state.score))}>{state.score}</span>
            )}
          </td>
          <td className="num hidden w-px font-mono sm:table-cell" data-testid="standard-findings">
            {audited ? state.findings_open ?? 0 : <span className="text-fg-muted">-</span>}
          </td>
          <td className="hidden whitespace-nowrap sm:table-cell">
            <span className="inline-flex items-center gap-2">
              <span className="text-fg-muted">{state.last_audit_at ? formatDate(state.last_audit_at) : "Never"}</span>
              {jobId && (
                <Link to={`/jobs/${jobId}`} title={`Audit job ${jobId}`} data-testid="standard-audit-job">
                  <Badge tone={jobStatus ? statusTone(jobStatus) : "neutral"}>
                    {jobStatus ? statusLabel(jobStatus) : "Audit job"}
                  </Badge>
                </Link>
              )}
            </span>
          </td>
          <td className="w-px">
            <Button
              type="button"
              size="xs"
              variant={gold === row.repo ? "default" : "outline"}
              disabled={reason !== null || starting}
              title={reason ?? "Start a read-only audit against the active standard"}
              onClick={() => startAudit(row.repo)}
              data-testid="standard-audit-button"
              data-gold={gold === row.repo ? "true" : undefined}
            >
              {starting ? "Starting…" : "Audit"}
            </Button>
          </td>
        </tr>
        {error && (
          <tr data-testid="standard-repo-error">
            <td colSpan={columns} className="text-xs text-destructive" role="alert">
              {row.repo}: {error}
            </td>
          </tr>
        )}
      </Fragment>
    );
  };

  if (rows.length === 0) {
    return (
      <EmptyState
        icon={FolderGit2}
        title="No registered repositories under this owner"
        description="Register repositories from the organization page; each one starts in mode off."
        className="py-10"
      />
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Filter by name"
          aria-label="Filter repositories"
          className="h-8 w-full text-xs sm:w-64"
          data-testid="standard-repos-filter"
        />
        <span className="text-xs text-muted-foreground" data-testid="standard-repos-counts">
          {matched ? `${matched.length} of ${rows.length}` : `${rows.length} ${rows.length === 1 ? "repository" : "repositories"}`}
          {` · ${counts.enforce} enforce · ${counts.advise} advise · ${counts.off} off`}
        </span>
      </div>
      {!isAdmin && (
        <p className="text-xs text-muted-foreground" data-testid="standard-mode-admin-note">
          An admin sets each repository&apos;s mode.
        </p>
      )}
      <div className={TABLE_BOX}>
        <table className="data-table" data-testid="standard-repos-table">
          <thead>
            <tr>
              <th scope="col">Repository</th>
              {!narrow && <th scope="col">Mode</th>}
              <th scope="col" className="num">Score</th>
              <th scope="col" className="num hidden sm:table-cell">Findings</th>
              <th scope="col" className="hidden sm:table-cell">Last audit</th>
              <th scope="col" className="w-px">
                <span className="sr-only">Audit</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {(matched ?? held).map(renderRow)}
            {!matched && held.length === 0 && (
              <tr data-testid="standard-repos-none-held">
                <td colSpan={columns} className="py-4 text-center text-sm text-muted-foreground">
                  No repository is held to this standard yet: an admin sets advise or enforce.
                </td>
              </tr>
            )}
            {!matched && folded.length > 0 && (
              <tr data-testid="standard-repos-off-row">
                <td colSpan={columns} className="py-1">
                  <Button
                    type="button"
                    size="xs"
                    variant="ghost"
                    aria-expanded={showOff}
                    onClick={() => setShowOff((v) => !v)}
                    data-testid="standard-repos-off-toggle"
                  >
                    {showOff ? `Hide the ${folded.length} in mode off` : `Show ${folded.length} in mode off`}
                  </Button>
                </td>
              </tr>
            )}
            {!matched && showOff && folded.map(renderRow)}
            {matched?.length === 0 && (
              <tr>
                <td colSpan={columns} className="py-6 text-center text-sm text-muted-foreground">
                  No repository matches this filter.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
