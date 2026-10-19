import { formatErrorDetail } from "@/api/errorDetail";
import type { RepoStandardState, Standard, StandardMode, StandardSchema } from "@/api/standards";
import type { BadgeTone } from "@/components/ui/Badge";
import { isTerminalStatus } from "@/lib/jobStatus";
import { sentenceCase, statusLabel } from "@/lib/status";

/**
 * Pure helpers for the Standards view: the owner's repositories with their
 * mode and newest audit, in the order that puts the furthest from the
 * standard first, and the standard's roles, type scale and rules as rows.
 */

export const MODE_LABELS: Readonly<Record<StandardMode, string>> = { off: "Off", advise: "Advise", enforce: "Enforce" };

/** What each mode means, from docs/standards.md, for a select's title. */
export const MODE_HINTS: Readonly<Record<StandardMode, string>> = {
  off: "embry0 does nothing for this repository under the standard: it is never audited",
  advise: "audits may run; their findings are advice",
  enforce: "audits may run; holding pull requests to the standard is reserved for a later release",
};

export function modeLabel(mode: string): string {
  return MODE_LABELS[mode as StandardMode] ?? sentenceCase(mode);
}

/** "design" -> "Design standard". */
export function standardTitle(kind: string): string {
  return `${sentenceCase(kind)} standard`;
}

/** The owner a repository sits under: its first path segment, lowercase, as the API stores it. */
export function ownerOf(repo: string): string {
  return (repo.split("/")[0] ?? "").toLowerCase();
}

export function defaultState(repo: string, owner: string, kind: string): RepoStandardState {
  return {
    repo,
    owner,
    kind,
    mode: "off",
    last_audit_at: null,
    last_audit_job_id: null,
    score: null,
    findings_open: null,
    exceptions: [],
    updated_at: null,
  };
}

export interface StandardRepoRow {
  /** The registry's spelling, which the mode and audit routes look up. */
  repo: string;
  state: RepoStandardState;
}

const MODE_RANK: Readonly<Record<string, number>> = { enforce: 0, advise: 1, off: 2 };

/** Score ascending with no score last; then enforce, advise, off; then name. */
export function compareRows(a: StandardRepoRow, b: StandardRepoRow): number {
  const sa = a.state.score;
  const sb = b.state.score;
  if (sa !== sb) {
    if (sa === null) return 1;
    if (sb === null) return -1;
    return sa - sb;
  }
  const mode = (MODE_RANK[a.state.mode] ?? 3) - (MODE_RANK[b.state.mode] ?? 3);
  if (mode !== 0) return mode;
  return a.repo.toLowerCase().localeCompare(b.repo.toLowerCase());
}

/**
 * Every registered repository under the owner, each with its stored state or
 * mode off, plus any stored state whose repository the registry list left
 * out (a deactivated one), so nothing with a mode is hidden. The state is
 * stored lowercase and the registry keeps the forge's spelling, so they are
 * matched without case.
 */
export function standardRows(
  owner: string,
  kind: string,
  registered: readonly { repo: string }[],
  states: readonly RepoStandardState[],
): StandardRepoRow[] {
  const byRepo = new Map(states.filter((s) => s.kind === kind).map((s) => [s.repo.toLowerCase(), s]));
  const rows: StandardRepoRow[] = [];
  const seen = new Set<string>();
  for (const r of registered) {
    const key = r.repo.toLowerCase();
    if (ownerOf(r.repo) !== owner || seen.has(key)) continue;
    seen.add(key);
    rows.push({ repo: r.repo, state: byRepo.get(key) ?? defaultState(r.repo, owner, kind) });
  }
  for (const [key, state] of byRepo) {
    if (!seen.has(key)) rows.push({ repo: state.repo, state });
  }
  return rows.sort(compareRows);
}

/**
 * True for a repository in mode off that no audit ever scored: nothing about
 * it under the standard to read. The Standards view folds these away, since
 * most of an owner's repositories start in this state and would bury the rest.
 * An off repository with a score (audited, then switched off) stays in view.
 */
export function isOffAndUnscored(row: StandardRepoRow): boolean {
  return row.state.mode === "off" && row.state.score === null;
}

export function modeCounts(rows: readonly StandardRepoRow[]): Record<StandardMode, number> {
  const counts: Record<StandardMode, number> = { off: 0, advise: 0, enforce: 0 };
  for (const row of rows) if (row.state.mode in counts) counts[row.state.mode] += 1;
  return counts;
}

/** Status of a 0 to 100 score: 80 and up holds the standard, under 50 is far from it. */
export function scoreTone(score: number | null): BadgeTone | null {
  if (score === null) return null;
  if (score >= 80) return "success";
  if (score >= 50) return "warning";
  return "error";
}

const SCORE_TEXT: Readonly<Record<string, string>> = {
  success: "text-success",
  warning: "text-warning",
  error: "text-destructive",
};

/** The text colour a score is shown in; empty for no score. */
export function scoreTextClass(score: number | null): string {
  const tone = scoreTone(score);
  return tone ? SCORE_TEXT[tone] : "";
}

/** True while the newest audit is known to be unfinished. An unknown status
 * (still loading, or the job is gone) is not busy: the server refuses a
 * second audit with a 409 that the page shows. */
export function isAuditBusy(jobStatus: string | undefined): boolean {
  return jobStatus !== undefined && !isTerminalStatus(jobStatus);
}

export interface AuditContext {
  hasStandard: boolean;
  canAudit: boolean;
  jobStatus: string | undefined;
}

/** Why a repository cannot be audited now, or null when it can. */
export function auditBlockedReason(row: StandardRepoRow, ctx: AuditContext): string | null {
  if (!ctx.canAudit) return "Starting an audit needs the operator tier.";
  if (row.state.mode === "off") return "In mode off: an admin sets advise or enforce before it can be audited.";
  if (!ctx.hasStandard) return "There is no active standard to audit against.";
  if (isAuditBusy(ctx.jobStatus)) return `An audit is already ${statusLabel(ctx.jobStatus).toLowerCase()}.`;
  return null;
}

/**
 * The one repository whose Audit button carries the gold: the first, in
 * table order, under the standard that has never completed an audit and can
 * be audited now. Null when there is none, and then nothing is gold.
 */
export function nextAuditRepo(
  rows: readonly StandardRepoRow[],
  blocked: (row: StandardRepoRow) => string | null,
): string | null {
  const next = rows.find((r) => r.state.mode !== "off" && r.state.last_audit_at === null && blocked(r) === null);
  return next ? next.repo : null;
}

/** Readable text for a failed standards request: the API's own detail first. */
export function requestErrorText(err: unknown, fallback: string): string {
  const response = (err as { response?: { status?: number; data?: { detail?: unknown } } } | null)?.response;
  const detail = formatErrorDetail(response?.data?.detail);
  if (detail) return detail;
  if (response?.status === 403) return "Your session's tier cannot do this.";
  return err instanceof Error && err.message ? err.message : fallback;
}

// ----- The standard itself -----

/** A role value as text: a font stack as a list, anything else as JSON. */
export function formatRoleValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(formatRoleValue).join(", ");
  return JSON.stringify(value);
}

const HEX = /^#(?:[0-9a-f]{3,4}|[0-9a-f]{6}|[0-9a-f]{8})$/i;
const FUNCTIONAL = /^(?:rgba?|hsla?)\(\s*[0-9.\s,%/+-]+\)$/i;
const NAMED = /^[a-z]{3,20}$/i;

/**
 * True for a plain CSS colour (hex, rgb/hsl, or a named colour). Only such a
 * value paints a swatch: the values are an admin's JSON, and a value such as
 * `url(...)` would otherwise make every viewer's browser fetch it.
 */
export function isPlainColour(value: unknown): value is string {
  return typeof value === "string" && (HEX.test(value) || FUNCTIONAL.test(value) || NAMED.test(value));
}

export interface RoleEntry {
  name: string;
  type: string | null;
  description: string | null;
  value: unknown;
  hasValue: boolean;
}

/** Every role the schema declares, by name, with this owner's value. */
export function roleEntries(standard: Pick<Standard, "schema" | "values">): RoleEntry[] {
  const roles = standard.schema.roles ?? {};
  return Object.keys(roles)
    .sort()
    .map((name) => {
      const spec = roles[name] ?? {};
      return {
        name,
        type: typeof spec.type === "string" ? spec.type : null,
        description: typeof spec.description === "string" ? spec.description : null,
        value: standard.values[name],
        hasValue: Object.prototype.hasOwnProperty.call(standard.values, name),
      };
    });
}

export function colourRoles(standard: Pick<Standard, "schema" | "values">): RoleEntry[] {
  return roleEntries(standard).filter((r) => r.type === "color");
}

export function otherRoles(standard: Pick<Standard, "schema" | "values">): RoleEntry[] {
  return roleEntries(standard).filter((r) => r.type !== "color");
}

export interface TypeStep {
  name: string;
  size: string;
  weight: string;
  /** The step's other keys, as `key: value` joined by a middle dot. */
  other: string;
}

function px(size: string): number {
  const n = Number.parseFloat(size);
  return Number.isFinite(n) ? n : -1;
}

/** The type steps, largest first; a size that is not a number goes last. */
export function typeScale(schema: StandardSchema): TypeStep[] {
  const steps = schema.type_scale ?? {};
  return Object.entries(steps)
    .map(([name, spec]) => {
      const { size, weight, ...rest } = spec ?? {};
      return {
        name,
        size: formatRoleValue(size),
        weight: formatRoleValue(weight),
        other: Object.entries(rest)
          .map(([key, value]) => `${key.replace(/_/g, " ")}: ${formatRoleValue(value)}`)
          .join(" · "),
      };
    })
    .sort((a, b) => px(b.size) - px(a.size) || a.name.localeCompare(b.name));
}
