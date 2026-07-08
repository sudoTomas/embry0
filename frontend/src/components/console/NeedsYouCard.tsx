import { memo, useEffect, useState } from "react";
import { Clock } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { QuestionsForm } from "@/components/jobs/QuestionsForm";
import { JobStatusBadge } from "@/components/jobs/JobStatusBadge";
import { answerInput } from "@/api/inputs";
import type { InterruptData } from "@/hooks/useAgentStates";
import type { JobInput, JobResponse } from "@/lib/types";
import { ConsoleCardShell } from "./ConsoleCardShell";

/** AwaitingInputCard's amber-glow treatment, reused verbatim so the board's
 * actionable cards read the same as the JobDetailPage banner. */
const AMBER_GLOW = {
  background: "linear-gradient(135deg, rgba(245,158,11,0.05), rgba(9,9,11,0.98))",
  border: "1px solid rgba(245,158,11,0.2)",
  boxShadow: "0 0 20px rgba(245,158,11,0.05)",
} as const;

/** Live "expires in 3h 12m" countdown from the interrupt's paused_at +
 * ttl_hours. Minute granularity, so a 30s tick keeps it honest without a
 * per-second re-render. Returns null when the interrupt carries no TTL. */
function useTtlCountdown(pausedAt: string | undefined, ttlHours: number | undefined): string | null {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, []);
  if (!pausedAt || !ttlHours) return null;
  const expiresAt = new Date(pausedAt).getTime() + ttlHours * 3_600_000;
  const remainingMs = expiresAt - now;
  if (remainingMs <= 0) return "expired";
  const hours = Math.floor(remainingMs / 3_600_000);
  const minutes = Math.floor((remainingMs % 3_600_000) / 60_000);
  return hours > 0 ? `expires in ${hours}h ${minutes}m` : `expires in ${minutes}m`;
}

interface NeedsYouCardProps {
  job: JobResponse;
  /** Interrupt payload (paused/awaiting jobs) — drives the TTL countdown and
   * the question fallback when no JobInput rows exist yet. */
  interrupt?: InterruptData | null;
  /** Persisted input rows; pending/auto_answered ones feed the inline form. */
  jobInputs?: JobInput[];
}

/**
 * Board card for a blocked job (awaiting_input / paused). A blocked job
 * silently expiring is the board's worst failure mode, so the card carries
 * an explicit TTL countdown, and the QuestionsForm renders inline so
 * answering never requires navigation.
 */
export const NeedsYouCard = memo(function NeedsYouCard({ job, interrupt, jobInputs }: NeedsYouCardProps) {
  const queryClient = useQueryClient();
  const ttl = useTtlCountdown(interrupt?.paused_at, interrupt?.ttl_hours);

  // Same pending + auto_answered surfacing as AwaitingInputCard: pending rows
  // gate interactivity; auto_answered ones render with the Override affordance.
  const renderable = (jobInputs ?? []).filter(
    (i) => i.status === "pending" || i.status === "auto_answered",
  );
  const question = renderable[0]?.question ?? interrupt?.reason ?? null;

  // Map JobInput → QuestionsForm's PendingInput shape (AwaitingInputCard's
  // mapping, minus fields the board card doesn't carry).
  const formInputs = renderable.map((i) => ({
    id: i.input_id,
    question: i.question,
    options: i.options,
    category: i.category,
    auto_answer: i.auto_answer,
    // Anything wider than the form's status union is filtered out above.
    status: i.status as "pending" | "answered" | "auto_answered",
  }));

  // answerInput posts to /issues/{issueId}/inputs/{inputId}/answer.
  const issueIdByInput: Record<string, string> = {};
  for (const i of renderable) {
    issueIdByInput[i.input_id] = i.issue_id;
  }

  return (
    <ConsoleCardShell
      job={job}
      className="border-transparent"
      style={AMBER_GLOW}
      headerRight={<JobStatusBadge status={job.status} />}
    >
      <div className="mt-2 min-w-0">
        <span data-testid="needs-you-question" className="block text-xs text-amber-200/90 truncate">
          {question ?? "Pipeline paused — open the job for details"}
        </span>
      </div>
      {ttl && (
        <div
          data-testid="ttl-countdown"
          className={`mt-1.5 inline-flex items-center gap-1 text-[11px] ${
            ttl === "expired" ? "text-destructive" : "text-amber-400"
          }`}
        >
          <Clock className="w-3 h-3" />
          {ttl}
        </div>
      )}
      {formInputs.length > 0 && (
        // Inline answering — clicks inside the form must not trigger the
        // shell's card-level navigate.
        <div className="mt-3" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
          <QuestionsForm
            inputs={formInputs}
            onAnswer={async (inputId, answer) => {
              await answerInput(issueIdByInput[inputId], inputId, answer);
              await queryClient.invalidateQueries({ queryKey: ["job-inputs", job.job_id] });
            }}
          />
        </div>
      )}
    </ConsoleCardShell>
  );
});
