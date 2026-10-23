import { formatDate } from "@/lib/utils";
import { ACTION_LABELS, actionIcon, formatDetails } from "@/lib/auditLabels";
import type { ActivityEntry } from "@/lib/types";

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
          const Icon = actionIcon(entry.action);
          const label = ACTION_LABELS[entry.action] ?? entry.action;
          const detail = formatDetails(entry.action, entry.details);

          return (
            <div key={entry.id} className="relative flex items-start gap-3 pl-8">
              {/* Icon bubble */}
              <span className="absolute left-0 flex h-7 w-7 items-center justify-center rounded-full bg-card border border-white/[0.08] shrink-0">
                <Icon aria-hidden="true" className="h-3.5 w-3.5 text-fg-muted" />
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
