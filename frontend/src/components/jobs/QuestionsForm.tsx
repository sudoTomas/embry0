import { useState, type JSX } from "react";
import { Button } from "@/components/ui/Button";

interface PendingInput {
  id: string;
  question: string;
  options?: string[] | null;
  asking_node?: string | null;
  category?: string | null;
  // Plan B Task 6 — agent ask-user metadata so the form can render
  // auto-answered questions with an Override affordance.
  importance?: "blocking" | "auto_answerable";
  auto_answer?: string | null;
  // Mirrors the backend issue_inputs.status. "answered" is shown read-only.
  // "auto_answered" gets the suggestion + Override button. "pending" (or
  // unset, for legacy job-input rows) renders the answer field.
  status?: "pending" | "answered" | "auto_answered" | "skipped";
}

interface QuestionsFormProps {
  inputs: PendingInput[];
  onAnswer: (inputId: string, answer: string) => Promise<void>;
}

export function QuestionsForm({ inputs, onAnswer }: QuestionsFormProps): JSX.Element {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});
  // Tracks an auto-answered input the user is actively overriding.
  // Only one override is in-flight at a time to keep the UI simple.
  const [overridingId, setOverridingId] = useState<string | null>(null);

  if (inputs.length === 0) {
    return <p className="text-sm text-white/50 italic">No pending questions.</p>;
  }

  // An input "needs the user" if it's pending (status unset or "pending"),
  // OR the user has explicitly opted to override an auto-answered one.
  const needsAnswer = (input: PendingInput): boolean => {
    if (input.status === "answered" || input.status === "skipped") return false;
    if (input.status === "auto_answered") return overridingId === input.id;
    return true; // pending or legacy (status undefined)
  };

  const inputsNeedingAnswer = inputs.filter(needsAnswer);
  const allAnswered =
    inputsNeedingAnswer.length > 0 &&
    inputsNeedingAnswer.every((i) => (answers[i.id] ?? "").trim().length > 0);

  const handleSubmit = async () => {
    if (!allAnswered) return;
    setSubmitting(true);
    setErrors({});
    const toSubmit = inputsNeedingAnswer;
    const results = await Promise.allSettled(
      toSubmit.map((i) => onAnswer(i.id, answers[i.id])),
    );
    const newErrors: Record<string, string> = {};
    results.forEach((r, idx) => {
      if (r.status === "rejected") {
        newErrors[toSubmit[idx].id] = String(r.reason).slice(0, 200);
      }
    });
    setErrors(newErrors);
    // Clear override mode for inputs that submitted successfully so the parent
    // can re-render them as answered when the next refetch comes through.
    if (Object.keys(newErrors).length === 0) {
      setOverridingId(null);
    }
    setSubmitting(false);
  };

  return (
    <div className="space-y-4">
      {inputs.map((input, idx) => {
        const isAutoAnswered = input.status === "auto_answered";
        const isAnswered = input.status === "answered";
        const isOverriding = isAutoAnswered && overridingId === input.id;
        const showAnswerField = needsAnswer(input);

        return (
          <div
            key={input.id}
            className="border border-amber-500/20 rounded p-3 bg-amber-500/[0.03]"
          >
            <div className="flex items-baseline gap-2 mb-2">
              <span className="text-amber-400 font-mono text-xs">{idx + 1}.</span>
              {input.asking_node && (
                <span className="text-[10px] uppercase text-amber-400/60">
                  {input.asking_node}
                </span>
              )}
              <p className="text-sm text-white/90 flex-1">{input.question}</p>
            </div>

            {/* Read-only: an answer already exists (e.g. via Telegram). */}
            {isAnswered && (
              <p className="text-sm text-emerald-300/90 italic">
                Answered.
              </p>
            )}

            {/* Auto-answered: show the suggestion. Stays visible even while
                the user types an override so they can compare. */}
            {isAutoAnswered && (
              <div className="mb-2 px-2 py-1.5 rounded bg-emerald-500/[0.06] border border-emerald-500/20">
                <p className="text-xs uppercase tracking-wide text-emerald-400/70 mb-0.5">
                  Auto-answered
                </p>
                <p className="text-sm text-emerald-300/90 whitespace-pre-wrap">
                  {input.auto_answer ?? "(no suggestion recorded)"}
                </p>
              </div>
            )}

            {/* Override toggle — only when auto-answered and not already overriding. */}
            {isAutoAnswered && !isOverriding && (
              <Button
                variant="outline"
                size="sm"
                onClick={() => setOverridingId(input.id)}
                disabled={submitting}
                className="border-amber-500/40 text-amber-300 hover:bg-amber-500/10"
              >
                Override
              </Button>
            )}

            {/* Answer field — pending inputs OR auto-answered inputs being overridden. */}
            {showAnswerField && (
              <>
                {input.options && input.options.length > 0 ? (
                  <select
                    value={answers[input.id] ?? ""}
                    onChange={(e) =>
                      setAnswers((p) => ({ ...p, [input.id]: e.target.value }))
                    }
                    disabled={submitting}
                    className="w-full bg-black/40 border border-white/10 rounded px-2 py-1.5 text-sm text-white"
                  >
                    <option value="">— select —</option>
                    {input.options.map((opt) => (
                      <option key={opt} value={opt}>
                        {opt}
                      </option>
                    ))}
                  </select>
                ) : (
                  <textarea
                    value={answers[input.id] ?? ""}
                    onChange={(e) =>
                      setAnswers((p) => ({ ...p, [input.id]: e.target.value }))
                    }
                    disabled={submitting}
                    rows={2}
                    className="w-full bg-black/40 border border-white/10 rounded px-2 py-1.5 text-sm text-white font-mono resize-y"
                    placeholder={isOverriding ? "Override answer..." : "Your answer..."}
                  />
                )}
                {isOverriding && (
                  <button
                    type="button"
                    onClick={() => {
                      setOverridingId(null);
                      setAnswers((p) => {
                        const next = { ...p };
                        delete next[input.id];
                        return next;
                      });
                    }}
                    disabled={submitting}
                    className="mt-1 text-xs text-white/50 hover:text-white/80 underline"
                  >
                    Cancel override
                  </button>
                )}
              </>
            )}

            {errors[input.id] && (
              <p className="mt-1 text-xs text-red-400">{errors[input.id]}</p>
            )}
          </div>
        );
      })}
      {inputsNeedingAnswer.length > 0 && (
        <Button onClick={handleSubmit} disabled={!allAnswered || submitting}>
          {submitting
            ? "Submitting…"
            : `Submit ${inputsNeedingAnswer.length} answer${inputsNeedingAnswer.length !== 1 ? "s" : ""}`}
        </Button>
      )}
    </div>
  );
}
