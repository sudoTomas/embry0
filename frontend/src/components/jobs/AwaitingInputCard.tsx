import { PauseCircle } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { QuestionsForm } from "@/components/jobs/QuestionsForm";
import { answerInput } from "@/api/inputs";
import type { AwaitingInputEvent } from "@/lib/types";
import type { JobInput } from "@/lib/types";

interface AwaitingInputCardProps {
  pendingInputs: Record<string, AwaitingInputEvent>;
  jobInputs: JobInput[];
  jobId: string;
}

export function AwaitingInputCard({ pendingInputs, jobInputs, jobId }: AwaitingInputCardProps) {
  const queryClient = useQueryClient();

  const pendingList = Object.values(pendingInputs);
  // Plan B Task 6: surface auto_answered rows alongside pending so the user
  // can review and override the agent's suggestion before the pipeline moves
  // on. We still gate "is the form interactive" on having at least one
  // pending row; auto_answered alone wouldn't normally hold the pipeline.
  const pending = (jobInputs ?? []).filter((i) => i.status === "pending");
  const autoAnswered = (jobInputs ?? []).filter((i) => i.status === "auto_answered");
  const renderable = [...pending, ...autoAnswered];

  // Nothing to show: no renderable job inputs and no in-flight events.
  if (renderable.length === 0 && pendingList.length === 0) return null;

  // Map JobInput → QuestionsForm's PendingInput shape, forwarding the new
  // ask-user metadata so the form can render the override affordance.
  const formInputs = renderable.map((i) => ({
    id: i.input_id,
    question: i.question,
    options: i.options,
    category: i.category,
    auto_answer: i.auto_answer,
    // JobInput.status uses a wider union ("rejected" | "timeout" too) than
    // QuestionsForm cares about; narrow to the form's status type. Anything
    // else is filtered out above so this cast is sound.
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
      className="rounded-2xl p-6"
      style={{
        background: "linear-gradient(135deg, rgba(245,158,11,0.05), rgba(9,9,11,0.98))",
        border: "1px solid rgba(245,158,11,0.2)",
        boxShadow: "0 0 20px rgba(245,158,11,0.05)",
      }}
    >
      <div className="flex items-center gap-3 mb-4">
        <div className="w-10 h-10 rounded-[10px] flex items-center justify-center bg-amber-500/12 border border-amber-500/25">
          <PauseCircle className="w-5 h-5 text-amber-500" />
        </div>
        <div>
          <h3 className="text-lg font-bold text-amber-400">Awaiting Input</h3>
          <p className="text-sm text-white/40">The pipeline needs more information to continue</p>
        </div>
      </div>

      {/* Fallback: surface in-flight question events when no JobInput rows exist yet */}
      {renderable.length === 0 && pendingList.length > 0 && (
        <div className="space-y-2 mb-4">
          {pendingList.map((input) => (
            <div key={input.input_id} className="px-3 py-2 rounded-lg bg-amber-500/5 border border-amber-500/10 text-sm text-amber-200">
              {input.question}
            </div>
          ))}
        </div>
      )}

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
