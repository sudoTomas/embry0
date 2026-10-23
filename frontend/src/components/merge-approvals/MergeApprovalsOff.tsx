import { GitMerge } from "lucide-react";
import { EmptyState } from "@/components/ui/EmptyState";

/**
 * A deployment with merge approvals off: the routes answer 503, which the
 * list and an ask's page both read as "off", an empty state rather than an
 * error for a feature the operator did not turn on.
 */
export function MergeApprovalsOff() {
  return (
    <EmptyState
      icon={GitMerge}
      title="Merge approvals are off on this deployment"
      description="Set MERGE_APPROVAL_MODE and the approval chat in the root .env to have embry0 ask before it merges."
    />
  );
}
