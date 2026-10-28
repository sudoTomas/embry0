import type { PackPreview } from "@/api/progressionPack";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { delta } from "@/lib/progressionPack";

const number = (n: number | null | undefined) => (n == null ? "—" : n.toLocaleString("en-US"));

function Cell({ before, after }: { before: number | null | undefined; after: number | null | undefined }) {
  const change = delta(before, after);
  return (
    <td className="px-2 py-1 text-right font-mono tabular-nums">
      <span className="text-fg-muted">{number(before)}</span>
      <span aria-hidden="true" className="px-1 text-fg-muted">
        →
      </span>
      <span className="sr-only"> to </span>
      <span className="text-fg">{number(after)}</span>
      {change && <span className={change.startsWith("+") ? "pl-1 text-success" : "pl-1 text-destructive"}>{change}</span>}
    </td>
  );
}

/**
 * What activating the draft would re-price, before (the pack in effect) and
 * after (the draft): each character's level and total level, the account's
 * totals, and the achievements unlocked. The table scrolls inside its own
 * box on a narrow screen, so the page never does.
 */
export function PreviewChanges({ preview }: { preview: PackPreview }) {
  const lookup = useIdentityLookup();
  const { changes } = preview;
  const moved = changes.characters.filter(
    (c) => c.before?.level !== c.after?.level || c.before?.total_level !== c.after?.total_level,
  ).length;
  return (
    <div className="space-y-3" data-testid="pack-preview">
      <p className="text-sm text-fg-secondary">
        {moved === 0
          ? "No character's level or total level would change."
          : `${moved} of ${changes.characters.length} characters would change level or total level.`}{" "}
        Achievements unlocked: {number(changes.achievements_unlocked.before)} →{" "}
        {number(changes.achievements_unlocked.after)}. Account total level: {number(changes.account.before.total_level)} →{" "}
        {number(changes.account.after.total_level)}.
      </p>
      {changes.characters.length > 0 && (
        <div className="max-w-full overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-xs" data-testid="pack-preview-table">
            <caption className="sr-only">Each character's level and total level, now and under the draft</caption>
            <thead>
              <tr className="border-b border-border text-left text-fg-muted">
                <th scope="col" className="px-2 py-1 font-medium">
                  Character
                </th>
                <th scope="col" className="px-2 py-1 text-right font-medium">
                  Level
                </th>
                <th scope="col" className="px-2 py-1 text-right font-medium">
                  Total level
                </th>
              </tr>
            </thead>
            <tbody>
              {changes.characters.map((c) => (
                <tr key={c.agent_type} className="border-b border-border last:border-0" data-testid="pack-preview-row">
                  <th scope="row" className="max-w-[10rem] px-2 py-1 text-left font-normal [overflow-wrap:anywhere]">
                    {lookup(c.agent_type).displayName}
                  </th>
                  <Cell before={c.before?.level} after={c.after?.level} />
                  <Cell before={c.before?.total_level} after={c.after?.total_level} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
