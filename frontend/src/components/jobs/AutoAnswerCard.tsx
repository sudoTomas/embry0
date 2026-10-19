import { useState } from "react";
import { useAnswerInput } from "@/hooks/useInputs";
import type { JobInput } from "@/lib/types";

interface AutoAnswerCardProps {
  input: JobInput;
  jobId: string;
}

/**
 * Historical record of an orchestrator auto-answer, with a post-hoc override.
 *
 * RAV-1059: overriding is just answering — the human answer goes through the
 * normal answer endpoint (which accepts auto_answered rows) and feeds the
 * accumulated Q&A context the agent sees on its next resume. There is no
 * separate reject flow; an overridden row is status "answered" with the
 * original auto_answer kept for the record.
 */
export function AutoAnswerCard({ input, jobId }: AutoAnswerCardProps) {
  const [showOverride, setShowOverride] = useState(false);
  const [override, setOverride] = useState("");
  const answerMutation = useAnswerInput();

  const handleOverride = () => {
    if (!override.trim()) return;
    answerMutation.mutate(
      { issueId: input.issue_id, jobId, inputId: input.id, answer: override },
      { onSuccess: () => setShowOverride(false) },
    );
  };

  const isOverridden = input.status === "answered" && input.auto_answer != null;

  return (
    <div className="rounded-md border border-white/[0.08] bg-white/[0.02] p-3 space-y-2 text-sm">
      <div className="flex items-center gap-2">
        <span className="text-[10px] uppercase tracking-wider text-white/30 bg-white/[0.06] rounded-full px-2 py-0.5">
          {input.asking_node}
        </span>
        <span className="text-white/40">
          {isOverridden ? "Auto-answer overridden" : "Orchestrator answered"}
        </span>
      </div>

      <p className="text-white/60">{input.question}</p>

      <div className="flex items-center gap-2">
        <span className="text-white/40 text-xs">Answer:</span>
        <span className={`text-white/80 ${isOverridden ? "line-through text-white/30" : ""}`}>
          {input.auto_answer}
        </span>
      </div>

      {isOverridden && input.answer && (
        <div className="flex items-center gap-2">
          <span className="text-white/40 text-xs">Overridden with:</span>
          <span className="text-amber-300">{input.answer}</span>
        </div>
      )}

      {!isOverridden && input.status === "auto_answered" && (
        <div className="flex items-center gap-2 pt-1">
          {!showOverride ? (
            <button
              onClick={() => setShowOverride(true)}
              className="text-xs text-white/40 hover:text-white/70 transition-colors underline underline-offset-2"
            >
              Override
            </button>
          ) : (
            <div className="flex items-center gap-2 w-full">
              <input
                type="text"
                value={override}
                onChange={(e) => setOverride(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") handleOverride(); }}
                placeholder="Your answer..."
                className="flex-1 bg-white/[0.04] border border-white/[0.08] rounded-md px-2 py-1 text-xs text-white/80 outline-none focus:border-amber-400/50 transition-colors"
                autoFocus
              />
              <button
                onClick={handleOverride}
                disabled={!override.trim() || answerMutation.isPending}
                className="px-2 py-1 text-xs rounded-md bg-amber-500 text-black hover:bg-amber-400 disabled:opacity-40 transition-colors"
              >
                {answerMutation.isPending ? "..." : "Submit"}
              </button>
              <button
                onClick={() => { setShowOverride(false); setOverride(""); }}
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
