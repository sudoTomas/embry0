import { Clock, Play, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/Button";
import type { AccountWait } from "@/lib/types/jobs";
import { formatDateTime } from "@/lib/utils";

interface AccountWaitBannerProps {
  wait: AccountWait;
  onTryNow: () => void;
  onCancel: () => void;
}

/** A job parked for a spent LLM account: what it waits for and when it goes on, in the viewer's own time. */
export function AccountWaitBanner({ wait, onTryNow, onCancel }: AccountWaitBannerProps) {
  const who = [wait.agent, wait.model && `on ${wait.model}`].filter(Boolean).join(" ");
  const title =
    wait.reason === "local_busy"
      ? "Waiting for a free slot on the local model"
      : `Waiting for the ${wait.provider} account (${wait.reason.replace(/_/g, " ")})`;
  return (
    <div className="rounded-lg border border-amber-500/20 bg-amber-500/[0.06] overflow-hidden" data-testid="account-wait-banner">
      <div className="px-4 py-3 flex items-center gap-3">
        <Clock className="w-4 h-4 text-amber-400" />
        <div className="flex-1">
          <div className="text-sm font-medium text-amber-300">{title}</div>
          <div className="text-xs text-amber-400/60 mt-0.5">
            Goes on at {formatDateTime(wait.until)}, or sooner once the account is ready again.
            {who && ` ${who} waits.`}
            {wait.chosen && " A person chose this model or key for the job, so it does not move to another one."}
          </div>
        </div>
      </div>
      <div className="px-4 py-3 flex items-center gap-2 border-t border-amber-500/10 bg-amber-500/[0.03]">
        <Button size="sm" onClick={onTryNow} className="gap-1.5">
          <Play className="w-3 h-3" /> Try now
        </Button>
        <div className="flex-1" />
        <Button variant="ghost" size="sm" onClick={onCancel} className="gap-1.5 text-destructive/60 hover:text-destructive">
          <Trash2 className="w-3 h-3" /> Cancel
        </Button>
      </div>
    </div>
  );
}
