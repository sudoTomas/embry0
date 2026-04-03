import { useState } from "react";
import { useRejectInput } from "@/hooks/useInputs";
import type { JobInput } from "@/lib/types";

interface AutoAnswerCardProps {
  input: JobInput;
  jobId: string;
}

export function AutoAnswerCard({ input, jobId }: AutoAnswerCardProps) {
  const [showReject, setShowReject] = useState(false);
  const [replacement, setReplacement] = useState("");
  const rejectMutation = useRejectInput();

  const handleReject = () => {
    if (!replacement.trim()) return;
    rejectMutation.mutate(
      { jobId, inputId: input.input_id, replacementAnswer: replacement },
      { onSuccess: () => setShowReject(false) },
    );
  };

  const isRejected = input.status === "rejected";

  return (
    <div className="rounded-md border border-white/[0.08] bg-white/[0.02] p-3 space-y-2 text-sm">
      <div className="flex items-center gap-2">
        <span className="text-[10px] uppercase tracking-wider text-white/30 bg-white/[0.06] rounded-full px-2 py-0.5">
          {input.category}
        </span>
        <span className="text-white/40">
          {isRejected ? "Auto-answer rejected" : "Orchestrator answered"}
        </span>
      </div>

      <p className="text-white/60">{input.question}</p>

      <div className="flex items-center gap-2">
        <span className="text-white/40 text-xs">Answer:</span>
        <span className={`text-white/80 ${isRejected ? "line-through text-white/30" : ""}`}>
          {input.auto_answer}
        </span>
      </div>

      {isRejected && input.answer && (
        <div className="flex items-center gap-2">
          <span className="text-white/40 text-xs">Replaced with:</span>
          <span className="text-amber-300">{input.answer}</span>
        </div>
      )}

      {!isRejected && input.status === "auto_answered" && (
        <div className="flex items-center gap-2 pt-1">
          {!showReject ? (
            <button
              onClick={() => setShowReject(true)}
              className="text-xs text-white/40 hover:text-white/70 transition-colors underline underline-offset-2"
            >
              Reject &amp; replace
            </button>
          ) : (
            <div className="flex items-center gap-2 w-full">
              <input
                type="text"
                value={replacement}
                onChange={(e) => setReplacement(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") handleReject(); }}
                placeholder="Replacement answer..."
                className="flex-1 bg-white/[0.04] border border-white/[0.08] rounded-md px-2 py-1 text-xs text-white/80 outline-none focus:border-amber-400/50 transition-colors"
                autoFocus
              />
              <button
                onClick={handleReject}
                disabled={!replacement.trim() || rejectMutation.isPending}
                className="px-2 py-1 text-xs rounded-md bg-amber-500 text-black hover:bg-amber-400 disabled:opacity-40 transition-colors"
              >
                {rejectMutation.isPending ? "..." : "Submit"}
              </button>
              <button
                onClick={() => { setShowReject(false); setReplacement(""); }}
                className="text-xs text-white/30 hover:text-white/60 transition-colors"
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
