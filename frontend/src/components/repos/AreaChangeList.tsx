import type { AreaChange } from "@/api/areas";

const KEY_WORDS: Record<string, string> = {
  name: "name",
  paths: "paths",
  logs: "log strings",
  owners: "owners",
  order: "its place in the list",
};

function changeWords(change: AreaChange): string {
  if (change.change === "added") return "added";
  if (change.change === "removed") return "removed";
  return `changes its ${change.keys.map((key) => KEY_WORDS[key] ?? key).join(", ")}`;
}

/**
 * What a change does to a repository's areas, area by area, the owners it adds or removes first: an owner is
 * asked about the area's work and may answer its decisions, so who becomes one is what a reviewer reads first.
 */
export function AreaChangeList({ changes }: { changes: AreaChange[] }) {
  if (changes.length === 0) return <p className="text-sm text-fg-muted">No area changes; only comments or layout do.</p>;
  return (
    <ul className="space-y-1 text-sm" data-testid="area-changes">
      {changes.map((change) => (
        <li key={`${change.change}:${change.area}`} data-testid="area-change">
          <span className="font-medium">{change.area}</span> {changeWords(change)}
          {change.owners_added.length > 0 && (
            <span className="text-warning" data-testid="area-owners-added">
              {" · makes "}
              {change.owners_added.join(", ")} {change.owners_added.length === 1 ? "an owner" : "owners"}
            </span>
          )}
          {change.owners_removed.length > 0 && (
            <span className="text-fg-secondary" data-testid="area-owners-removed">
              {" · no longer asks "}
              {change.owners_removed.join(", ")}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}
