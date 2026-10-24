import { useState, type FormEvent } from "react";
import type { Organization } from "@/api/orgs";
import { formatErrorDetail } from "@/api/errorDetail";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { usePatchOrganizationSettings } from "@/hooks/useOrgs";
import { CONTEXT_AUDIT_MAX_DAYS, CONTEXT_AUDIT_MIN_DAYS, contextAuditDays } from "@/lib/orgs";

function errorText(err: unknown): string {
  const response = (err as { response?: { status?: number; data?: { detail?: unknown } } } | null)?.response;
  if (response?.status === 403) return "Changing the schedule requires admin scope.";
  return formatErrorDetail(response?.data?.detail) ?? "The schedule was not saved.";
}

/** The field's text as a setting: empty is off (null), a whole number in range is that many days, anything else is invalid. */
function parseDays(text: string): { valid: boolean; days: number | null } {
  const trimmed = text.trim();
  if (trimmed === "") return { valid: true, days: null };
  const n = Number(trimmed);
  const valid = /^\d+$/.test(trimmed) && n >= CONTEXT_AUDIT_MIN_DAYS && n <= CONTEXT_AUDIT_MAX_DAYS;
  return { valid, days: valid ? n : null };
}

const daysText = (days: number) => `${days} ${days === 1 ? "day" : "days"}`;

/**
 * How often embry0 audits the files that steer a coding agent (CLAUDE.md,
 * AGENTS.md, skills, rules) in the owner's registered repositories: again
 * after N days once the branch moved, 1 to 365, or off. A change to one of
 * those files is audited sooner. Built like RediscoverSetting beside it: an
 * admin changes it in place, where an empty field is off and Turn off
 * empties it; anyone else reads it. No checkbox and no gold, for the same
 * reasons.
 */
export function ContextAuditSetting({ org, isAdmin }: { org: Organization; isAdmin: boolean }) {
  const current = contextAuditDays(org);
  const patch = usePatchOrganizationSettings(org.owner);
  // The field as edited; null while it shows the stored value.
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!isAdmin) {
    return (
      <p className="text-sm text-fg-secondary" data-testid="org-context-audit-setting">
        {current === null ? "The context audit is off." : `Audits agent instructions every ${daysText(current)}.`}
      </p>
    );
  }

  const text = draft ?? (current === null ? "" : String(current));
  const { valid, days: next } = parseDays(text);
  const dirty = draft !== null && (!valid || next !== current);

  const edit = (value: string) => {
    setError(null);
    setDraft(value);
  };
  const save = (e: FormEvent) => {
    e.preventDefault();
    if (!dirty || !valid) return;
    patch.mutate(
      { context_audit_days: next },
      { onSuccess: () => setDraft(null), onError: (err) => setError(errorText(err)) },
    );
  };

  return (
    <form onSubmit={save} className="flex flex-wrap items-center gap-2 text-sm" data-testid="org-context-audit-setting">
      <label htmlFor="org-context-audit-days" className="text-fg-secondary">
        Audit agent instructions every
      </label>
      <Input
        id="org-context-audit-days"
        type="text"
        inputMode="numeric"
        value={text}
        onChange={(e) => edit(e.target.value)}
        disabled={patch.isPending}
        aria-label="Days between context audits, empty for off"
        className="h-7 w-16 px-2 text-xs"
        data-testid="org-context-audit-days"
      />
      <span className="text-fg-secondary">days</span>
      {valid && next === null && <span className="text-xs text-fg-muted">(off)</span>}
      {!dirty && current !== null && (
        <Button
          type="button"
          size="xs"
          variant="ghost"
          onClick={() => edit("")}
          disabled={patch.isPending}
          data-testid="org-context-audit-off"
        >
          Turn off
        </Button>
      )}
      {dirty && (
        <>
          <Button
            type="submit"
            size="xs"
            variant="outline"
            disabled={!valid || patch.isPending}
            data-testid="org-context-audit-save"
          >
            {patch.isPending ? "Saving…" : "Save"}
          </Button>
          <Button
            type="button"
            size="xs"
            variant="ghost"
            onClick={() => {
              setDraft(null);
              setError(null);
            }}
            disabled={patch.isPending}
          >
            Cancel
          </Button>
          <span className="text-xs text-fg-muted" data-testid="org-context-audit-current">
            {current === null ? "now off" : `now every ${daysText(current)}`}
          </span>
        </>
      )}
      {!valid && (
        <span className="text-xs text-destructive" data-testid="org-context-audit-invalid">
          A whole number of days from {CONTEXT_AUDIT_MIN_DAYS} to {CONTEXT_AUDIT_MAX_DAYS}, or empty for off.
        </span>
      )}
      {error && (
        <span className="text-xs text-destructive" role="alert" data-testid="org-context-audit-error">
          {error}
        </span>
      )}
    </form>
  );
}
