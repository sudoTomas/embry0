import { PauseCircle } from "lucide-react";
import { InputForm } from "@/components/jobs/InputForm";
import type { AwaitingInputEvent } from "@/lib/types";
import type { JobInput } from "@/lib/types";

interface AwaitingInputCardProps {
  pendingInputs: Record<string, AwaitingInputEvent>;
  jobInputs: JobInput[];
  jobId: string;
}

export function AwaitingInputCard({ pendingInputs, jobInputs, jobId }: AwaitingInputCardProps) {
  const pendingList = Object.values(pendingInputs);
  if (pendingList.length === 0) return null;

  // Match pending input events with job inputs
  const inputsToShow = jobInputs.filter(
    (input) => input.status === "pending" && pendingInputs[input.input_id],
  );

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

      {/* Show questions from pending inputs */}
      {pendingList.length > 0 && inputsToShow.length === 0 && (
        <div className="space-y-2 mb-4">
          {pendingList.map((input) => (
            <div key={input.input_id} className="px-3 py-2 rounded-lg bg-amber-500/5 border border-amber-500/10 text-sm text-amber-200">
              {input.question}
            </div>
          ))}
        </div>
      )}

      {/* Input forms */}
      <div className="space-y-3">
        {inputsToShow.map((input) => (
          <InputForm key={input.input_id} input={input} jobId={jobId} />
        ))}
      </div>
    </div>
  );
}
