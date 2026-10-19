import { useState, type FormEvent } from "react";
import type { Organization } from "@/api/orgs";
import { formatErrorDetail } from "@/api/errorDetail";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { usePatchOrganizationSettings } from "@/hooks/useOrgs";
import { REDISCOVER_MAX_HOURS, REDISCOVER_MIN_HOURS, rediscoverHours } from "@/lib/orgs";

/** The form as edited; null while it shows the stored value. */
interface Draft {
  on: boolean;
  hours: string;
}

/** Hours offered when the schedule is switched on from off. */
const DEFAULT_HOURS = "24";

function errorText(err: unknown): string {
  const response = (err as { response?: { status?: number; data?: { detail?: unknown } } } | null)?.response;
  if (response?.status === 403) return "Changing the schedule requires admin scope.";
  return formatErrorDetail(response?.data?.detail) ?? "The schedule was not saved.";
}

function parseHours(text: string): number | null {
  const n = Number(text);
  return text.trim() !== "" && Number.isInteger(n) && n >= REDISCOVER_MIN_HOURS && n <= REDISCOVER_MAX_HOURS ? n : null;
}

/**
 * How often discovery walks the owner again on its own: every N hours, 1 to
 * 168, or off. An admin changes it in place; anyone else reads it.
 */
export function RediscoverSetting({ org, isAdmin }: { org: Organization; isAdmin: boolean }) {
  const current = rediscoverHours(org);
  const patch = usePatchOrganizationSettings(org.owner);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (!isAdmin) {
    return (
      <p className="text-sm text-fg-secondary" data-testid="org-rediscover-setting">
        {current === null ? "Rediscovery is off." : `Rediscovers every ${current} ${current === 1 ? "hour" : "hours"}.`}
      </p>
    );
  }

  const shown: Draft = draft ?? { on: current !== null, hours: current === null ? "" : String(current) };
  const hours = parseHours(shown.hours);
  const valid = !shown.on || hours !== null;
  const next = shown.on ? hours : null;
  const dirty = draft !== null && (!valid || next !== current);

  const edit = (change: Partial<Draft>) => {
    setError(null);
    const merged = { ...shown, ...change };
    if (change.on && merged.hours.trim() === "") merged.hours = DEFAULT_HOURS;
    setDraft(merged);
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
      <label className="inline-flex items-center gap-2 text-fg-secondary">
        {/* Neutral, not the accent: the view's one gold belongs to what most needs acting on. */}
        <input
          type="checkbox"
          className="accent-fg-secondary"
          checked={shown.on}
          onChange={(e) => edit({ on: e.target.checked })}
          disabled={patch.isPending}
          data-testid="org-rediscover-on"
        />
        Rediscover every
      </label>
      <Input
        type="number"
        inputMode="numeric"
        min={REDISCOVER_MIN_HOURS}
        max={REDISCOVER_MAX_HOURS}
        step={1}
        value={shown.hours}
        placeholder={DEFAULT_HOURS}
        onChange={(e) => edit({ hours: e.target.value })}
        disabled={!shown.on || patch.isPending}
        aria-label="Hours between discovery runs"
        className="h-7 w-16 px-2 text-xs"
        data-testid="org-rediscover-hours"
      />
      <span className="text-fg-secondary">hours</span>
      {!dirty && current === null && <span className="text-xs text-fg-muted">(off)</span>}
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
          A whole number of hours from {REDISCOVER_MIN_HOURS} to {REDISCOVER_MAX_HOURS}.
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
