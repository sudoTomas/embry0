import { memo } from "react";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
import { useAgentIdentity } from "@/hooks/useAgentIdentity";
import { useTemplate, useTemplates } from "@/hooks/usePipelines";
import { formatDate } from "@/lib/utils";
import type { JobResponse } from "@/lib/types";
import { ConsoleCardShell } from "./ConsoleCardShell";
import { firstPartyMember } from "./firstPartyMember";

interface QueuedCardProps {
  job: JobResponse;
  /** 1-based position in the pending queue, when the board can compute it. */
  position?: number | null;
  /** Total pending depth (from useQueue), giving "position 2 of 5" context. */
  queueDepth?: number | null;
}

/** Static board card for a pending job — no WS, just queue-position context. */
export const QueuedCard = memo(function QueuedCard({ job, position, queueDepth }: QueuedCardProps) {
  const { data: templates } = useTemplates();
  const templateId = templates?.find((t) => t.name === job.pipeline_template)?.template_id ?? null;
  const { data: template } = useTemplate(templateId);
  const identity = useAgentIdentity(firstPartyMember(template?.graph));
  return (
    <ConsoleCardShell job={job}>
      <div className="mt-2 flex items-center gap-3 text-[11px] text-white/40">
        <span data-testid="party-sprite">
          <AgentAvatar identity={identity} size={32} state="pending" />
        </span>
        <span data-testid="queue-position">
          {position != null
            ? queueDepth != null
              ? `position ${position} of ${queueDepth}`
              : `position ${position}`
            : "waiting in queue"}
        </span>
        <span>created {formatDate(job.created_at)}</span>
      </div>
    </ConsoleCardShell>
  );
});
