import { ISSUE_STATUS_COLORS, ISSUE_STATUS_BG_COLORS, ISSUE_STATUS_ICONS } from "@/lib/constants";

interface IssueStatusBadgeProps {
  status: string;
}

export function IssueStatusBadge({ status }: IssueStatusBadgeProps) {
  const icon = ISSUE_STATUS_ICONS[status] ?? "●";
  const color = ISSUE_STATUS_COLORS[status] ?? "text-muted-foreground";
  const bg = ISSUE_STATUS_BG_COLORS[status] ?? "bg-muted";
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${bg} ${color}`}>
      <span>{icon}</span>
      {status.replace("_", " ")}
    </span>
  );
}
