import type { SettingsChange } from "@/lib/types/settings";
import { formatSettingValue } from "@/lib/settingsDocument";

/**
 * One side of a change: the value a loop reads when the server sent it (a
 * preview, a save), else the stored value. An unwritten key says so.
 */
function Side({ set, stored, effective }: { set: boolean; stored: unknown; effective: unknown }) {
  if (effective !== undefined) {
    return (
      <span className="font-mono text-fg">
        {formatSettingValue(effective)}
        {!set && <span className="font-sans text-fg-muted"> (default)</span>}
      </span>
    );
  }
  return set ? (
    <span className="font-mono text-fg">{formatSettingValue(stored)}</span>
  ) : (
    <span className="text-fg-muted">not set</span>
  );
}

/** Each key a change touches: its value before and after. */
export function SettingsChangeList({ changes }: { changes: SettingsChange[] }) {
  return (
    <ul className="divide-y divide-border rounded-lg border border-border" data-testid="settings-changes">
      {changes.map((change) => (
        <li key={change.key} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-3 py-2 text-sm" data-testid="settings-change">
          <code className="font-mono text-fg [overflow-wrap:anywhere]">{change.key}</code>
          <span className="flex min-w-0 flex-wrap items-baseline gap-x-2 [overflow-wrap:anywhere]">
            <Side set={change.set_before} stored={change.before} effective={change.effective_before} />
            <span className="text-fg-muted" aria-hidden="true">→</span>
            <span className="sr-only">becomes</span>
            <Side set={change.set_after} stored={change.after} effective={change.effective_after} />
          </span>
        </li>
      ))}
    </ul>
  );
}
