import { Badge } from "@/components/ui/Badge";
import { statusLabel, statusTone } from "@/lib/status";
import type { AppStatus, RunOverallStatus } from "@/lib/types";

interface Props {
  status: AppStatus | RunOverallStatus;
}

/** Status pill for a run or per-app QA result; words and tone from `@/lib/status`. */
export function RunStatusBadge({ status }: Props) {
  return (
    <Badge tone={statusTone(status)} title={status}>
      {statusLabel(status)}
    </Badge>
  );
}
