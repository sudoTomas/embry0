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
  const pending = (jobInputs ?? []).filter((i) => i.status === "pending");

  // Nothing to show: no pending job inputs and no in-flight events.
  if (pending.length === 0 && pendingList.length === 0) return null;

  // Map JobInput → QuestionsForm's PendingInput shape.
  const formInputs = pending.map((i) => ({
    id: i.input_id,
    question: i.question,
    options: i.options,
    category: i.category,
  }));

  // We need issue_id to hit POST /issues/{issueId}/inputs/{inputId}/answer.
  // Build a quick lookup from input_id → issue_id.
  const issueIdByInput: Record<string, string> = {};
  for (const i of pending) {
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
      {pending.length === 0 && pendingList.length > 0 && (
        <div className="space-y-2 mb-4">
          {pendingList.map((input) => (
            <div key={input.input_id} className="px-3 py-2 rounded-lg bg-amber-500/5 border border-amber-500/10 text-sm text-amber-200">
              {input.question}
            </div>
          ))}
        </div>
      )}

      {/* Batched multi-question form */}
      {pending.length > 0 && (
        <QuestionsForm
          inputs={formInputs}
          onAnswer={async (inputId, answer) => {
            const issueId = issueIdByInput[inputId];
            await answerInput(issueId, inputId, answer);
            await queryClient.invalidateQueries({ queryKey: ["job-inputs", jobId] });
          }}
        />
      )}
    </div>
  );
}
