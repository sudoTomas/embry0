import { memo } from "react";
import { QuestionsForm } from "@/components/jobs/QuestionsForm";
import { JobStatusBadge } from "@/components/jobs/JobStatusBadge";
import { useAnswerInput } from "@/hooks/useInputs";
import type { JobInput, JobResponse } from "@/lib/types";
import { ConsoleCardShell } from "./ConsoleCardShell";

/** AwaitingInputCard's amber treatment, reused verbatim so the board's
 * actionable cards read the same as the JobDetailPage banner: the surface with
 * an amber border, no glow. */
const AMBER_GLOW = {
  background: "var(--color-surface)",
  border: "1px solid color-mix(in srgb, var(--color-amber) 35%, transparent)",
} as const;

interface NeedsYouCardProps {
  job: JobResponse;
  /** Persisted input rows; pending/auto_answered ones feed the inline form. */
  jobInputs?: JobInput[];
}

/**
 * Board card for a blocked job (awaiting_input / paused). The QuestionsForm
 * renders inline so answering never requires navigation.
 */
export const NeedsYouCard = memo(function NeedsYouCard({ job, jobInputs }: NeedsYouCardProps) {
  const answer = useAnswerInput();

  // Same pending + auto_answered surfacing as AwaitingInputCard: pending rows
  // gate interactivity; auto_answered ones render with the Override affordance.
  const renderable = (jobInputs ?? []).filter(
    (i) => i.status === "pending" || i.status === "auto_answered",
  );
  const question = renderable[0]?.question ?? null;

  // Map JobInput → QuestionsForm's PendingInput shape (AwaitingInputCard's
  // mapping, minus fields the board card doesn't carry).
  const formInputs = renderable.map((i) => ({
    id: i.id,
    question: i.question,
    asking_node: i.asking_node,
    auto_answer: i.auto_answer,
    // Anything wider than the form's status union is filtered out above.
    status: i.status as "pending" | "answered" | "auto_answered",
  }));

  // An issue-backed question answers through its issue, an issue-less one
  // through the job (answerInput picks the route).
  const issueIdByInput: Record<string, string | null> = {};
  for (const i of renderable) {
    issueIdByInput[i.id] = i.issue_id;
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
      {formInputs.length > 0 && (
        // Inline answering — clicks inside the form must not trigger the
        // shell's card-level navigate.
        <div className="mt-3" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
          <QuestionsForm
            framed={false}
            inputs={formInputs}
            onAnswer={async (inputId, text) => {
              await answer.mutateAsync({ issueId: issueIdByInput[inputId], jobId: job.job_id, inputId, answer: text });
            }}
          />
        </div>
      )}
    </ConsoleCardShell>
  );
});
