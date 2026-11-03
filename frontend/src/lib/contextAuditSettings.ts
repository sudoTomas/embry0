// An organization's keys of the settings document's `context_audit` section
// (#170): whether and how often its repositories are audited, whether an
// audit opens a pull request, and which files it reads. Each is the
// organization's own when its layer sets it, else what it inherits from the
// deployment's document; `null` written with PATCH hands it back. The
// helpers here are pure, so the form and its tests share them.

import type { EffectiveSetting } from "@/lib/types/settings";

/** The section, as the settings document names it. */
export const CONTEXT_AUDIT_SECTION = "context_audit";

/** The keys an organization may set in its layer, in the order the form shows them. */
export const ORG_CONTEXT_AUDIT_KEYS = ["days", "open_prs", "files", "exclude_dirs"] as const;
export type OrgContextAuditKey = (typeof ORG_CONTEXT_AUDIT_KEYS)[number];

/** The bounds of `days`, as the section checks them. */
export const AUDIT_MIN_DAYS = 1;
export const AUDIT_MAX_DAYS = 365;

/** `days` as the schedule reads it: a whole number of days in range, else off (null). */
export function auditDays(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= AUDIT_MIN_DAYS && value <= AUDIT_MAX_DAYS
    ? value
    : null;
}

/** A schedule in a person's words. */
export function scheduleText(value: unknown): string {
  const days = auditDays(value);
  return days === null ? "off" : `every ${days} ${days === 1 ? "day" : "days"}`;
}

/** A list key's value in a person's words: its entries, or the built-in list when it is null. */
export function listText(value: unknown): string {
  return Array.isArray(value) ? (value.length === 0 ? "none" : value.join(", ")) : "the built-in list";
}

/** A pull-request switch in a person's words. */
export function pullRequestsText(value: unknown): string {
  return value === true ? "on" : "off";
}

/** What the form edits for one key: the organization's own value, or what it inherits. */
export interface Draft {
  /** `inherit`: the layer does not set the key. Otherwise how it sets it. */
  mode: string;
  /** The text of the field the mode shows: the days, the globs one per line, the directories. */
  text: string;
}

/** The layer sets the key: the value it holds, else it inherits. */
export function ownValue(setting: EffectiveSetting | undefined): { own: boolean; value: unknown } {
  return { own: setting?.source === "organization", value: setting?.value };
}

/** The form's starting draft for a key, from what the organization reads now. */
export function initialDraft(key: OrgContextAuditKey, setting: EffectiveSetting | undefined): Draft {
  const { own, value } = ownValue(setting);
  if (key === "days") {
    if (!own) return { mode: "inherit", text: String(auditDays(setting?.value) ?? "") };
    const days = auditDays(value);
    return days === null ? { mode: "off", text: "" } : { mode: "every", text: String(days) };
  }
  if (key === "open_prs") {
    if (!own) return { mode: "inherit", text: "" };
    return { mode: value === true ? "on" : "off", text: "" };
  }
  const shown = (Array.isArray(value) ? value.map(String) : []).join(key === "exclude_dirs" ? ", " : "\n");
  // A list the layer holds as null (written in a file) is the built-in list, which the form shows as inherited.
  if (!own || !Array.isArray(value)) return { mode: "inherit", text: shown };
  return { mode: "own", text: shown };
}

/** The entries of a list field: one per line, or for directories also comma-separated, trimmed, none empty. */
export function listEntries(text: string, commas: boolean): string[] {
  const parts = commas ? text.split(/[\n,]/) : text.split("\n");
  return parts.map((part) => part.trim()).filter((part) => part !== "");
}

/** What PATCH writes for a draft: null hands the key back, anything else is the layer's own value. */
export function draftValue(key: OrgContextAuditKey, draft: Draft): unknown {
  if (draft.mode === "inherit") return null;
  if (key === "days") return draft.mode === "off" ? false : Number(draft.text.trim());
  if (key === "open_prs") return draft.mode === "on";
  return listEntries(draft.text, key === "exclude_dirs");
}

/** Why a draft cannot be saved, in a person's words; null when it can. The server checks the rest. */
export function draftProblem(key: OrgContextAuditKey, draft: Draft): string | null {
  if (key === "days" && draft.mode === "every") {
    const text = draft.text.trim();
    const n = Number(text);
    if (!/^\d+$/.test(text) || n < AUDIT_MIN_DAYS || n > AUDIT_MAX_DAYS) {
      return `A whole number of days from ${AUDIT_MIN_DAYS} to ${AUDIT_MAX_DAYS}.`;
    }
  }
  if (key === "files" && draft.mode === "own" && listEntries(draft.text, false).length === 0) {
    return "Name at least one glob, or use the deployment's list.";
  }
  return null;
}

/**
 * The value the layer holds for a key now, as PATCH would write it: null when it does not set it. A schedule it
 * holds as null (written in a file) is off, as the schedule reads it; a list it holds as null is the built-in one.
 */
export function storedValue(key: OrgContextAuditKey, setting: EffectiveSetting | undefined): unknown {
  const { own, value } = ownValue(setting);
  if (!own) return null;
  if (key === "days") return auditDays(value) ?? false;
  return value ?? null;
}

/** The keys a save sends: those whose draft writes something other than what the layer holds now. */
export function changedValues(
  drafts: Record<OrgContextAuditKey, Draft>,
  settings: Partial<Record<OrgContextAuditKey, EffectiveSetting>>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const key of ORG_CONTEXT_AUDIT_KEYS) {
    const next = draftValue(key, drafts[key]);
    const now = storedValue(key, settings[key]);
    if (JSON.stringify(next) !== JSON.stringify(now)) out[key] = next;
  }
  return out;
}

/** The organization's caps of its context audits, keys of the `budget` section its layer may set (#171). */
export const BUDGET_SECTION = "budget";
export interface CapField {
  key: "context_audit_job_usd" | "context_audit_max_jobs_per_tick" | "fix_job_usd";
  label: string;
  help: string;
  /** A whole number from 0 to 50; else a number of US dollars more than 0. */
  count: boolean;
}
export const CAP_FIELDS: readonly CapField[] = [
  {
    key: "context_audit_job_usd",
    label: "Hard cap per audit (USD)",
    help: "Each audit job stops when it spends this, whatever the overrun mode says: nobody watches it.",
    count: false,
  },
  {
    key: "context_audit_max_jobs_per_tick",
    label: "Audits started per wake",
    help: "The most audits one wake of the schedule starts for the organization; the rest wait for the next. 0 starts none.",
    count: true,
  },
  {
    key: "fix_job_usd",
    label: "Hard cap per finding's fix (USD)",
    help: "The job that fixes one finding an owner accepted from the proposals stops when it spends this.",
    count: false,
  },
];

/** The form's starting draft for a cap: the organization's own value, or what it inherits. */
export function capDraft(setting: EffectiveSetting | undefined): Draft {
  const { own, value } = ownValue(setting);
  return { mode: own ? "own" : "inherit", text: typeof value === "number" ? String(value) : "" };
}

/** Why a cap's draft cannot be saved; null when it can. */
export function capProblem(field: CapField, draft: Draft): string | null {
  if (draft.mode !== "own") return null;
  const text = draft.text.trim();
  const n = Number(text);
  if (field.count) return /^\d+$/.test(text) && n <= 50 ? null : "A whole number from 0 to 50.";
  return text !== "" && Number.isFinite(n) && n > 0 ? null : "A number of US dollars more than 0.";
}

/** The caps a save sends: those whose draft differs from what the layer holds; null hands one back. */
export function changedCaps(
  drafts: Record<string, Draft>,
  settings: Partial<Record<string, EffectiveSetting>>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const field of CAP_FIELDS) {
    const draft = drafts[field.key];
    const next = draft.mode === "inherit" ? null : Number(draft.text.trim());
    const { own, value } = ownValue(settings[field.key]);
    const now = own ? value : null;
    if (next !== now) out[field.key] = next;
  }
  return out;
}
