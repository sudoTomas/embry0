import { useState } from "react";
import { useAnswerInput } from "@/hooks/useInputs";
import type { JobInput } from "@/lib/types";

interface InputFormProps {
  input: JobInput;
  jobId: string;
}

export function InputForm({ input, jobId }: InputFormProps) {
  const [selected, setSelected] = useState<string>("");
  const [customAnswer, setCustomAnswer] = useState("");
  const answerMutation = useAnswerInput();

  const hasOptions = input.options && input.options.length > 0;
  const effectiveAnswer = hasOptions ? selected : customAnswer;

  const handleSubmit = () => {
    if (!effectiveAnswer.trim()) return;
    answerMutation.mutate({ jobId, inputId: input.input_id, answer: effectiveAnswer });
  };

  return (
    <div className="rounded-lg border-2 border-amber-500/40 bg-amber-500/5 p-4 space-y-3">
      <div className="flex items-center gap-2">
        <div className="h-2 w-2 rounded-full bg-amber-400 animate-pulse" />
        <h3 className="text-sm font-semibold text-amber-300">Awaiting Your Input</h3>
        <span className="ml-auto text-[10px] uppercase tracking-wider text-white/30 bg-white/[0.06] rounded-full px-2 py-0.5">
          {input.category}
        </span>
      </div>

      <p className="text-sm text-white/80 whitespace-pre-wrap">{input.question}</p>

      {hasOptions ? (
        <div className="space-y-1.5">
          {input.options!
            .flatMap((opt) => opt.includes(" | ") ? opt.split(" | ").map((s) => s.trim()) : [opt])
            .filter(Boolean)
            .map((opt) => (
            <label
              key={opt}
              className="flex items-center gap-2 cursor-pointer text-sm text-white/70 hover:text-white/90 transition-colors"
            >
              <input
                type="radio"
                name={`input-${input.input_id}`}
                value={opt}
                checked={selected === opt}
                onChange={() => setSelected(opt)}
                className="accent-amber-400"
              />
              {opt}
            </label>
          ))}
        </div>
      ) : (
        <input
          type="text"
          value={customAnswer}
          onChange={(e) => setCustomAnswer(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") handleSubmit(); }}
          placeholder="Type your answer..."
          className="w-full bg-white/[0.04] border border-white/[0.08] rounded-md px-3 py-2 text-sm text-white/80 outline-none focus:border-amber-400/50 transition-colors"
        />
      )}

      <button
        onClick={handleSubmit}
        disabled={!effectiveAnswer.trim() || answerMutation.isPending}
        className="px-4 py-1.5 text-sm font-medium rounded-md bg-amber-500 text-black hover:bg-amber-400 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
      >
        {answerMutation.isPending ? "Submitting..." : "Submit Answer"}
      </button>
    </div>
  );
}
