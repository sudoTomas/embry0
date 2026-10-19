import { useQueue } from "@/hooks/useQueue";
import { Heartbeat, type HeartbeatTone } from "@/components/vitals";
import { cn } from "@/lib/utils";

const PILL: Record<HeartbeatTone, string> = {
  primary: "bg-primary-dim text-primary",
  live: "bg-teal-dim text-success",
  down: "bg-red-dim text-destructive",
  waiting: "bg-white/[0.06] text-fg-muted",
};

/**
 * The orchestrator's live state, pinned at the foot of the sidebar. Read from
 * the queue poll the sidebar already runs (same query key), so it costs no
 * extra request: answering means live, a failed read means unreachable.
 */
export function OrchestratorStatus({ collapsed }: { collapsed: boolean }) {
  const queue = useQueue();
  let tone: HeartbeatTone = "waiting";
  let label = "Connecting to the orchestrator";
  if (queue.isError) {
    tone = "down";
    label = "Orchestrator unreachable";
  } else if (queue.data) {
    tone = "live";
    label = "Orchestrator live";
  }
  return (
    <div
      data-testid="orchestrator-status"
      data-state={tone}
      className={cn(
        "flex h-[26px] items-center justify-center rounded-md",
        collapsed ? "mx-auto w-[26px]" : "mx-2.5",
        PILL[tone],
      )}
    >
      <Heartbeat label={label} tone={tone} hideLabel={collapsed} />
    </div>
  );
}
