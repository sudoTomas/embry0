import { Badge } from "@/components/ui/Badge";
import { statusLabel, statusTone } from "@/lib/status";

interface IssueStatusBadgeProps {
  status: string;
}

export function IssueStatusBadge({ status }: IssueStatusBadgeProps) {
  return (
    <Badge tone={statusTone(status)} title={status}>
      {statusLabel(status)}
    </Badge>
  );
}
