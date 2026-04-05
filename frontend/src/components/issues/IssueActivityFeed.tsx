import { formatDate } from "@/lib/utils";
import type { ActivityEntry } from "@/lib/types";

const ACTION_ICONS: Record<string, string> = {
  "issue.created": "✦",
  "issue.updated": "✎",
  "issue.status_changed": "→",
  "issue.triaged": "🔍",
  "issue.decomposed": "⑃",
  "issue.job_created": "⚡",
  "issue.github_synced": "⇄",
  "issue.cancelled": "✕",
};

const ACTION_LABELS: Record<string, string> = {
  "issue.created": "Created",
  "issue.updated": "Updated",
  "issue.status_changed": "Status changed",
  "issue.triaged": "Triaged",
  "issue.decomposed": "Decomposed",
  "issue.job_created": "Job created",
  "issue.github_synced": "GitHub synced",
  "issue.cancelled": "Cancelled",
};

function formatDetails(action: string, details: Record<string, unknown>): string {
  if (action === "issue.status_changed") {
    const from = details.old_status ?? details.from;
    const to = details.new_status ?? details.to;
    if (from && to) return `${from} → ${to}`;
  }
  if (action === "issue.updated") {
    const fields = details.fields ?? details.changed_fields;
    if (Array.isArray(fields) && fields.length > 0) return fields.join(", ");
    if (typeof fields === "string") return fields;
  }
  if (action === "issue.github_synced") {
    const direction = details.direction ?? details.sync_direction;
    if (direction) return String(direction);
  }
  return "";
}

interface IssueActivityFeedProps {
  entries: ActivityEntry[];
}

export function IssueActivityFeed({ entries }: IssueActivityFeedProps) {
  if (entries.length === 0) {
    return (
      <p className="text-sm text-muted-foreground py-4 text-center">No activity yet.</p>
    );
  }

  return (
    <div className="relative">
      {/* Left timeline border */}
      <div className="absolute left-3.5 top-0 bottom-0 border-l-2 border-white/[0.06]" />

      <div className="space-y-4">
        {entries.map((entry) => {
          const icon = ACTION_ICONS[entry.action] ?? "●";
          const label = ACTION_LABELS[entry.action] ?? entry.action;
          const detail = formatDetails(entry.action, entry.details);

          return (
            <div key={entry.id} className="relative flex items-start gap-3 pl-8">
              {/* Icon bubble */}
              <span className="absolute left-0 flex h-7 w-7 items-center justify-center rounded-full bg-card border border-white/[0.08] text-sm shrink-0">
                {icon}
              </span>

              {/* Content */}
              <div className="flex-1 min-w-0 pt-0.5">
                <div className="flex flex-wrap items-baseline gap-1.5 text-sm">
                  <span className="font-medium text-white/80">{label}</span>
                  {detail && (
                    <span className="text-muted-foreground text-xs font-mono">{detail}</span>
                  )}
                </div>
                <div className="flex items-center gap-2 mt-0.5 text-xs text-muted-foreground">
                  <span>{entry.actor}</span>
                  <span>·</span>
                  <span>{formatDate(entry.created_at)}</span>
                </div>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
