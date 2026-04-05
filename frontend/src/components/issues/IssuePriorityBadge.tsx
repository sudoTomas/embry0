import { ISSUE_PRIORITY_BG_COLORS } from "@/lib/constants";

interface IssuePriorityBadgeProps {
  priority: string;
}

export function IssuePriorityBadge({ priority }: IssuePriorityBadgeProps) {
  const bg = ISSUE_PRIORITY_BG_COLORS[priority] ?? "bg-zinc-500/20 text-zinc-400";
  return (
    <span className={`inline-flex items-center rounded-md px-2 py-0.5 text-xs font-medium ${bg}`}>
      {priority}
    </span>
  );
}
