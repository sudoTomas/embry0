import { PauseCircle } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { QuestionsForm } from "@/components/jobs/QuestionsForm";
import { answerInput } from "@/api/inputs";
import type { JobInput } from "@/lib/types";

interface AwaitingInputCardProps {
  jobInputs: JobInput[];
  jobId: string;
}

export function AwaitingInputCard({ jobInputs, jobId }: AwaitingInputCardProps) {
  const queryClient = useQueryClient();

  // Plan B Task 6: surface auto_answered rows alongside pending so the user
  // can review and override the agent's suggestion before the pipeline moves
  // on. We still gate "is the form interactive" on having at least one
  // pending row; auto_answered alone wouldn't normally hold the pipeline.
  const pending = (jobInputs ?? []).filter((i) => i.status === "pending");
  const autoAnswered = (jobInputs ?? []).filter((i) => i.status === "auto_answered");
  const renderable = [...pending, ...autoAnswered];

  // Nothing to show: no renderable job inputs.
  if (renderable.length === 0) return null;

  // Map JobInput → QuestionsForm's PendingInput shape, forwarding the new
  // ask-user metadata so the form can render the override affordance.
  const formInputs = renderable.map((i) => ({
    id: i.input_id,
    question: i.question,
    options: i.options,
    category: i.category,
    auto_answer: i.auto_answer,
    // JobInput.status also carries "skipped", which QuestionsForm doesn't
    // render; anything else is filtered out above so this cast is sound.
    status: i.status as "pending" | "answered" | "auto_answered",
  }));

  // We need issue_id to hit POST /issues/{issueId}/inputs/{inputId}/answer.
  // Build a quick lookup from input_id → issue_id.
  const issueIdByInput: Record<string, string> = {};
  for (const i of renderable) {
    issueIdByInput[i.input_id] = i.issue_id;
  }

  return (
    <div
      className="rounded-lg p-5"
      style={{
        background: "var(--color-surface)",
        border: "1px solid color-mix(in srgb, var(--color-amber) 35%, transparent)",
      }}
    >
      <div className="flex items-center gap-3 mb-4">
        <div className="w-10 h-10 rounded-[10px] flex items-center justify-center bg-amber-500/12 border border-amber-500/25">
          <PauseCircle className="w-5 h-5 text-amber-500" />
        </div>
        <div>
          <h3 className="text-lg font-bold text-amber-400">Awaiting input</h3>
          <p className="text-sm text-white/40">The pipeline needs more information to continue</p>
        </div>
      </div>

      {/* Batched multi-question form (pending + auto_answered with override) */}
      {renderable.length > 0 && (
        <QuestionsForm
          inputs={formInputs}
          onAnswer={async (inputId, answer) => {
            const issueId = issueIdByInput[inputId];
            await answerInput(issueId, inputId, answer, jobId);
            await queryClient.invalidateQueries({ queryKey: ["job-inputs", jobId] });
          }}
        />
      )}
    </div>
  );
}
