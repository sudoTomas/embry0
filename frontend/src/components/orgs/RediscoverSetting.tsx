import { useState, type FormEvent } from "react";
import type { Organization } from "@/api/orgs";
import { formatErrorDetail } from "@/api/errorDetail";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { usePatchOrganizationSettings } from "@/hooks/useOrgs";
import { REDISCOVER_MAX_HOURS, REDISCOVER_MIN_HOURS, rediscoverHours } from "@/lib/orgs";

function errorText(err: unknown): string {
  const response = (err as { response?: { status?: number; data?: { detail?: unknown } } } | null)?.response;
  if (response?.status === 403) return "Changing the schedule requires admin scope.";
  return formatErrorDetail(response?.data?.detail) ?? "The schedule was not saved.";
}

/** The field's text as a setting: empty is off (null), a whole number in range is that many hours, anything else is invalid. */
function parseHours(text: string): { valid: boolean; hours: number | null } {
  const trimmed = text.trim();
  if (trimmed === "") return { valid: true, hours: null };
  const n = Number(trimmed);
  const valid = /^\d+$/.test(trimmed) && n >= REDISCOVER_MIN_HOURS && n <= REDISCOVER_MAX_HOURS;
  return { valid, hours: valid ? n : null };
}

/**
 * How often discovery walks the owner again on its own: every N hours, 1 to
 * 168, or off. An admin changes it in place, where an empty field is off and
 * Turn off empties it; anyone else reads it. There is no checkbox: the page's
 * checkboxes pick needs in the act panel, in the accent, and a second checked
 * style would break the one selection style, while the accent here would
 * spend the view's one gold on a setting.
 */
export function RediscoverSetting({ org, isAdmin }: { org: Organization; isAdmin: boolean }) {
  const current = rediscoverHours(org);
  const patch = usePatchOrganizationSettings(org.owner);
  // The field as edited; null while it shows the stored value.
  const [draft, setDraft] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!isAdmin) {
    return (
      <p className="text-sm text-fg-secondary" data-testid="org-rediscover-setting">
        {current === null ? "Rediscovery is off." : `Rediscovers every ${current} ${current === 1 ? "hour" : "hours"}.`}
      </p>
    );
  }

  const text = draft ?? (current === null ? "" : String(current));
  const { valid, hours: next } = parseHours(text);
  const dirty = draft !== null && (!valid || next !== current);

  const edit = (value: string) => {
    setError(null);
    setDraft(value);
  };
  const save = (e: FormEvent) => {
    e.preventDefault();
    if (!dirty || !valid) return;
    patch.mutate(
      { rediscover_hours: next },
      { onSuccess: () => setDraft(null), onError: (err) => setError(errorText(err)) },
    );
  };

  return (
    <form onSubmit={save} className="flex flex-wrap items-center gap-2 text-sm" data-testid="org-rediscover-setting">
      <label htmlFor="org-rediscover-hours" className="text-fg-secondary">
        Rediscover every
      </label>
      <Input
        id="org-rediscover-hours"
        type="text"
        inputMode="numeric"
        value={text}
        onChange={(e) => edit(e.target.value)}
        disabled={patch.isPending}
        aria-label="Hours between discovery runs, empty for off"
        className="h-7 w-16 px-2 text-xs"
        data-testid="org-rediscover-hours"
      />
      <span className="text-fg-secondary">hours</span>
      {valid && next === null && <span className="text-xs text-fg-muted">(off)</span>}
      {!dirty && current !== null && (
        <Button
          type="button"
          size="xs"
          variant="ghost"
          onClick={() => edit("")}
          disabled={patch.isPending}
          data-testid="org-rediscover-off"
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
            data-testid="org-rediscover-save"
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
          <span className="text-xs text-fg-muted" data-testid="org-rediscover-current">
            {current === null ? "now off" : `now every ${current} ${current === 1 ? "hour" : "hours"}`}
          </span>
        </>
      )}
      {!valid && (
        <span className="text-xs text-destructive" data-testid="org-rediscover-invalid">
          A whole number of hours from {REDISCOVER_MIN_HOURS} to {REDISCOVER_MAX_HOURS}, or empty for off.
        </span>
      )}
      {error && (
        <span className="text-xs text-destructive" role="alert" data-testid="org-rediscover-error">
          {error}
        </span>
      )}
    </form>
  );
}
