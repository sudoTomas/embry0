import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Play, ScanEye, Wrench } from "lucide-react";
import {
  isMissingEndpoint,
  runOrganizationAction,
  type OrgActionName,
  type OrgActionResult,
  type OrgNeed,
  type OrgNeeds,
} from "@/api/orgs";
import { formatErrorDetail } from "@/api/errorDetail";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { EmptyState } from "@/components/ui/EmptyState";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { useNeedStatus } from "@/hooks/useOrgs";
import { actionLabel, groupNeeds, isActionable, planActions } from "@/lib/orgs";
import { cn } from "@/lib/utils";
import { NeedsEndpointMissing } from "./OrgNeedsList";
import { statusLabel } from "@/lib/status";
import { TABLE_BOX, TD, TH } from "./styles";

interface ResultRow extends OrgActionResult {
  action: OrgActionName;
}

interface RunOutcome {
  /** The selection the rows were produced for. */
  key: string;
  dryRun: boolean;
  rows: ResultRow[];
  error: string | null;
}

const OUTCOME_TONE: Record<OrgActionResult["outcome"], BadgeTone> = {
  done: "success",
  already: "neutral",
  skipped: "warning",
  failed: "error",
};

function errorText(err: unknown): string {
  const response = (err as { response?: { status?: number; data?: { detail?: unknown } } }).response;
  if (response?.status === 403) return "Acting on needs requires admin scope.";
  return formatErrorDetail(response?.data?.detail) ?? (err instanceof Error ? err.message : "The request failed.");
}

function selectionKey(ids: ReadonlySet<string>): string {
  return [...ids].sort().join("\n");
}

function ResultsTable({ outcome }: { outcome: RunOutcome }) {
  return (
    <div className="space-y-2" data-testid={outcome.dryRun ? "org-act-preview" : "org-act-results"}>
      <h3 className="text-sm font-semibold">{outcome.dryRun ? "Preview (dry run, nothing changed)" : "Applied"}</h3>
      {outcome.error && (
        <p className="text-sm text-destructive" role="alert" data-testid="org-act-error">
          {outcome.error}
        </p>
      )}
      {outcome.rows.length > 0 && (
        <div className={TABLE_BOX}>
          <table className="data-table min-w-[560px]">
            <thead>
              <tr>
                <th scope="col" className={TH}>Action</th>
                <th scope="col" className={TH}>Repository</th>
                <th scope="col" className={TH}>Outcome</th>
                <th scope="col" className={TH}>Detail</th>
              </tr>
            </thead>
            <tbody>
              {outcome.rows.map((r) => (
                <tr
                  key={`${r.action}:${r.repo}`}
                  data-testid="org-act-result-row"
                >
                  <td className={cn(TD, "whitespace-nowrap")}>{actionLabel(r.action)}</td>
                  <td className={cn(TD, "font-mono text-xs")}>{r.repo}</td>
                  <td className={TD}>
                    <Badge tone={OUTCOME_TONE[r.outcome] ?? "neutral"}>{statusLabel(r.outcome)}</Badge>
                  </td>
                  <td className={cn(TD, "text-white/60")}>{r.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function NeedRow({
  need,
  checked,
  onToggle,
  onStatus,
  disabled,
}: {
  need: OrgNeed;
  checked: boolean;
  onToggle: () => void;
  onStatus: (dismiss: boolean) => void;
  disabled: boolean;
}) {
  const actionable = isActionable(need);
  return (
    <li className="flex items-start gap-2 py-1.5" data-testid="org-act-need" data-need={need.id}>
      {actionable ? (
        <input
          type="checkbox"
          className="mt-1 accent-primary"
          checked={checked}
          onChange={onToggle}
          disabled={disabled}
          aria-label={`Select ${need.title}`}
          data-testid="org-act-checkbox"
        />
      ) : (
        <span className="mt-1 inline-block h-[13px] w-[13px] shrink-0" aria-hidden />
      )}
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <span className={need.status === "open" ? "" : "text-white/40"}>{need.title}</span>
          {actionable ? (
            <Badge tone="gold">{actionLabel(need.action.type)}</Badge>
          ) : need.status === "open" ? (
            <Badge tone="neutral" title="embry0 cannot do this one for you">
              By hand
            </Badge>
          ) : (
            <Badge tone="neutral">{statusLabel(need.status)}</Badge>
          )}
          <span className="text-xs text-muted-foreground">
            {need.repos.length} {need.repos.length === 1 ? "repository" : "repositories"}
          </span>
        </div>
        {need.detail && <p className="text-xs text-muted-foreground">{need.detail}</p>}
      </div>
      {need.status !== "done" && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 px-2 text-xs"
          onClick={() => onStatus(need.status === "open")}
          disabled={disabled}
          data-testid={need.status === "open" ? "org-need-dismiss" : "org-need-reopen"}
        >
          {need.status === "open" ? "Dismiss" : "Reopen"}
        </Button>
      )}
    </li>
  );
}

/**
 * Act on needs: tick the ones embry0 can do, preview them as a dry run, then
 * apply. Apply stays off until the current selection has a clean preview.
 */
export function OrgActPanel({
  owner,
  needs,
  isAdmin,
}: {
  owner: string;
  needs: OrgNeeds | null | undefined;
  isAdmin: boolean;
}) {
  const qc = useQueryClient();
  const status = useNeedStatus(owner);
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [preview, setPreview] = useState<RunOutcome | null>(null);
  const [applied, setApplied] = useState<RunOutcome | null>(null);
  const [busy, setBusy] = useState(false);
  const [missing, setMissing] = useState(false);
  const [statusNote, setStatusNote] = useState<string | null>(null);

  if (needs === undefined) return <Skeleton className="h-24 w-full" />;
  if (needs === null) return <NeedsEndpointMissing />;
  if (missing) {
    return (
      <EmptyState
        icon={Wrench}
        title="This server has no actions endpoint yet"
        description="Once embry0 is upgraded, the needs ticked here can be previewed and applied in one step."
        className="py-10"
      />
    );
  }

  const open = needs.needs.filter((n) => n.status === "open");
  const closed = needs.needs.filter((n) => n.status !== "open");
  const actionable = open.filter(isActionable);
  const key = selectionKey(selected);
  const plan = planActions(needs.needs.filter((n) => selected.has(n.id)));
  const previewFresh = preview !== null && preview.key === key && preview.error === null;

  const change = (next: Set<string>) => {
    setSelected(next);
    setPreview(null);
  };
  const toggle = (id: string) => {
    const next = new Set(selected);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    change(next);
  };
  const toggleMany = (ids: string[], on: boolean) => {
    const next = new Set(selected);
    ids.forEach((id) => (on ? next.add(id) : next.delete(id)));
    change(next);
  };

  const run = async (dryRun: boolean) => {
    setBusy(true);
    const rows: ResultRow[] = [];
    let error: string | null = null;
    for (const step of plan) {
      try {
        const res = await runOrganizationAction(owner, { action: step.action, repos: step.repos, dry_run: dryRun });
        rows.push(...res.results.map((r) => ({ ...r, action: step.action })));
      } catch (err) {
        if (isMissingEndpoint(err)) {
          setMissing(true);
          setBusy(false);
          return;
        }
        error = `${actionLabel(step.action)}: ${errorText(err)}`;
        break;
      }
    }
    const outcome: RunOutcome = { key, dryRun, rows, error };
    if (dryRun) {
      setPreview(outcome);
      setApplied(null);
    } else {
      setApplied(outcome);
      setPreview(null);
      setSelected(new Set());
      void qc.invalidateQueries({ queryKey: ["orgs", owner] });
      void qc.invalidateQueries({ queryKey: ["repos"] });
    }
    setBusy(false);
  };

  const setNeedStatus = (id: string, dismiss: boolean) => {
    setStatusNote(null);
    if (dismiss && selected.has(id)) toggle(id);
    status.mutate(
      { id, dismiss },
      {
        onError: (err) =>
          setStatusNote(
            isMissingEndpoint(err) ? "This server cannot dismiss or reopen needs yet." : errorText(err),
          ),
      },
    );
  };

  const disabled = !isAdmin || busy;

  return (
    <div className="space-y-4" data-testid="org-act-panel">
      {!isAdmin && (
        <p className="text-sm text-warning" data-testid="org-act-admin-note">
          Acting on needs requires admin scope.
        </p>
      )}
      {open.length === 0 ? (
        <p className="text-sm text-muted-foreground">No open needs.</p>
      ) : (
        <div className="space-y-3">
          {actionable.length > 0 && (
            <label className="flex items-center gap-2 text-xs text-muted-foreground">
              <input
                type="checkbox"
                className="accent-primary"
                checked={actionable.every((n) => selected.has(n.id))}
                onChange={(e) => toggleMany(actionable.map((n) => n.id), e.target.checked)}
                disabled={disabled}
                data-testid="org-act-select-all"
              />
              Select every need embry0 can act on ({actionable.length})
            </label>
          )}
          {groupNeeds(open).map((g) => (
            <section key={g.kind} className="rounded-lg border border-white/[0.06] px-3 py-2" data-testid="org-act-group">
              <h3 className="label-caps">
                {g.label} ({g.needs.length})
              </h3>
              <ul className="divide-y divide-white/[0.04]">
                {g.needs.map((n) => (
                  <NeedRow
                    key={n.id}
                    need={n}
                    checked={selected.has(n.id)}
                    onToggle={() => toggle(n.id)}
                    onStatus={(dismiss) => setNeedStatus(n.id, dismiss)}
                    disabled={disabled || status.isPending}
                  />
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}

      {statusNote && (
        <p className="text-sm text-warning" data-testid="org-need-status-note">
          {statusNote}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() => void run(true)}
          disabled={disabled || plan.length === 0}
          data-testid="org-act-preview-button"
        >
          <ScanEye className="h-4 w-4" /> Preview
        </Button>
        <Button
          type="button"
          size="sm"
          onClick={() => void run(false)}
          disabled={disabled || plan.length === 0 || !previewFresh}
          data-testid="org-act-apply-button"
        >
          <Play className="h-4 w-4" /> Apply
        </Button>
        <span className="text-xs text-muted-foreground">
          {plan.length === 0
            ? "Tick a need to preview it."
            : `${plan.map((p) => `${actionLabel(p.action)} ${p.repos.length}`).join(", ")}${previewFresh ? "" : ". Preview first."}`}
        </span>
      </div>

      {preview && <ResultsTable outcome={preview} />}
      {applied && <ResultsTable outcome={applied} />}

      {closed.length > 0 && (
        <details>
          <summary className="cursor-pointer text-xs text-muted-foreground">
            Done and dismissed ({closed.length})
          </summary>
          <ul className="divide-y divide-white/[0.04]">
            {closed.map((n) => (
              <NeedRow
                key={n.id}
                need={n}
                checked={false}
                onToggle={() => undefined}
                onStatus={(dismiss) => setNeedStatus(n.id, dismiss)}
                disabled={disabled || status.isPending}
              />
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}
