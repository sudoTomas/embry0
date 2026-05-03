import { useState, type JSX } from "react";
import { Button } from "@/components/ui/Button";

interface PendingInput {
  id: string;
  question: string;
  options?: string[] | null;
  asking_node?: string | null;
  category?: string | null;
}

interface QuestionsFormProps {
  inputs: PendingInput[];
  onAnswer: (inputId: string, answer: string) => Promise<void>;
}

export function QuestionsForm({ inputs, onAnswer }: QuestionsFormProps): JSX.Element {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [errors, setErrors] = useState<Record<string, string>>({});

  if (inputs.length === 0) {
    return <p className="text-sm text-white/50 italic">No pending questions.</p>;
  }

  const allAnswered = inputs.every((i) => (answers[i.id] ?? "").trim().length > 0);

  const handleSubmit = async () => {
    if (!allAnswered) return;
    setSubmitting(true);
    setErrors({});
    const results = await Promise.allSettled(
      inputs.map((i) => onAnswer(i.id, answers[i.id]))
    );
    const newErrors: Record<string, string> = {};
    results.forEach((r, idx) => {
      if (r.status === "rejected") {
        newErrors[inputs[idx].id] = String(r.reason).slice(0, 200);
      }
    });
    setErrors(newErrors);
    setSubmitting(false);
  };

  return (
    <div className="space-y-4">
      {inputs.map((input, idx) => (
        <div key={input.id} className="border border-amber-500/20 rounded p-3 bg-amber-500/[0.03]">
          <div className="flex items-baseline gap-2 mb-2">
            <span className="text-amber-400 font-mono text-xs">{idx + 1}.</span>
            {input.asking_node && (
              <span className="text-[10px] uppercase text-amber-400/60">
                {input.asking_node}
              </span>
            )}
            <p className="text-sm text-white/90 flex-1">{input.question}</p>
          </div>
          {input.options && input.options.length > 0 ? (
            <select
              value={answers[input.id] ?? ""}
              onChange={(e) => setAnswers((p) => ({ ...p, [input.id]: e.target.value }))}
              disabled={submitting}
              className="w-full bg-black/40 border border-white/10 rounded px-2 py-1.5 text-sm text-white"
            >
              <option value="">— select —</option>
              {input.options.map((opt) => (
                <option key={opt} value={opt}>{opt}</option>
              ))}
            </select>
          ) : (
            <textarea
              value={answers[input.id] ?? ""}
              onChange={(e) => setAnswers((p) => ({ ...p, [input.id]: e.target.value }))}
              disabled={submitting}
              rows={2}
              className="w-full bg-black/40 border border-white/10 rounded px-2 py-1.5 text-sm text-white font-mono resize-y"
              placeholder="Your answer..."
            />
          )}
          {errors[input.id] && (
            <p className="mt-1 text-xs text-red-400">{errors[input.id]}</p>
          )}
        </div>
      ))}
      <Button onClick={handleSubmit} disabled={!allAnswered || submitting}>
        {submitting ? "Submitting…" : `Submit ${inputs.length} answer${inputs.length !== 1 ? "s" : ""}`}
      </Button>
    </div>
  );
}
