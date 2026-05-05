import { Badge } from "@/components/ui/Badge";
import type { AppStatus, RunOverallStatus } from "@/lib/types";

const TONE_BY_STATUS: Record<AppStatus | RunOverallStatus, "success" | "warning" | "error" | "neutral"> = {
  passed: "success",
  failed: "error",
  infra_error: "neutral",
  qa_failure: "error",
  e2e_failure: "error",
  boot_failure: "warning",
  ready_check_failed: "warning",
  infra_failure: "neutral",
  skipped: "neutral",
  inconclusive: "warning",
};

interface Props {
  status: AppStatus | RunOverallStatus;
}

/**
 * Status pill for a run or per-app QA result. Maps the 8 SubTaskStatus
 * values + 2 run-overall values to a Badge tone.
 */
export function RunStatusBadge({ status }: Props) {
  const tone = TONE_BY_STATUS[status] ?? "neutral";
  return (
    <Badge tone={tone} title={status}>
      {status.replace(/_/g, " ")}
    </Badge>
  );
}
