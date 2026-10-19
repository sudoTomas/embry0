import { Badge } from "@/components/ui/Badge";
import { statusLabel, statusTone } from "@/lib/status";
import type { JobStatus } from "@/lib/types";

export function JobStatusBadge({ status }: { status: JobStatus }) {
  return (
    <Badge tone={statusTone(status)} title={status}>
      {statusLabel(status)}
    </Badge>
  );
}
