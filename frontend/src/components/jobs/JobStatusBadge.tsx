import { JOB_STATUS_COLORS, JOB_STATUS_BG_COLORS } from "@/lib/constants";
import type { JobStatus } from "@/lib/types";

export function JobStatusBadge({ status }: { status: JobStatus }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${JOB_STATUS_COLORS[status] ?? "text-muted-foreground"} ${JOB_STATUS_BG_COLORS[status] ?? "bg-muted/10"}`}
    >
      {status}
    </span>
  );
}
