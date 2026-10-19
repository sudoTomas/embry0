import { ISSUE_PRIORITY_BG_COLORS } from "@/lib/constants";
import { statusLabel } from "@/lib/status";

interface IssuePriorityBadgeProps {
  priority: string;
}

export function IssuePriorityBadge({ priority }: IssuePriorityBadgeProps) {
  const bg = ISSUE_PRIORITY_BG_COLORS[priority] ?? "bg-white/[0.06] text-fg-secondary";
  return (
    <span className={`inline-flex items-center whitespace-nowrap rounded px-1.5 py-0.5 text-2xs font-semibold ${bg}`}>
      {statusLabel(priority)}
    </span>
  );
}
