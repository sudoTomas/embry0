import { useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { toastApiError } from "@/api/errorToast";
import { answerInput, fetchJobInputs } from "@/api/inputs";
import type { Party } from "@/api/world";
import { useJobEventStream } from "@/hooks/useJobEventStream";
import { useRecall } from "@/hooks/useWorld";
import { describeEvent, formatSeconds } from "@/lib/world";

/** How many lines of what the party does the watch keeps. */
export const WATCH_LINES = 14;

/**
 * Watching a party at work: the step it is on, its last tool calls and
 * words, its question if it asked one (answer it here), and Recall.
 */
export function WatchParty({ party, canAct }: { party: Party; canAct: boolean }) {
  const [lines, setLines] = useState<{ seq: number; text: string }[]>([]);
  const counter = useRef(0);
  const { isConnected, isComplete } = useJobEventStream(
    party.job_id,
    {
      onEvent: (event) => {
        const line = describeEvent(event);
        if (!line) return;
        counter.current += 1;
        const seq = counter.current;
        setLines((prev) => [...prev, { seq, text: line }].slice(-WATCH_LINES));
      },
      onReset: () => setLines([]),
    },
    lines.length > 0,
  );
  const inputs = useQuery({
    queryKey: ["world", "inputs", party.job_id],
    queryFn: () => fetchJobInputs(party.job_id),
    enabled: party.state === "waiting",
    refetchInterval: 10_000,
  });
  const question = inputs.data?.find((i) => i.status === "pending");
  const [answer, setAnswer] = useState("");
  const [answering, setAnswering] = useState(false);
  const recall = useRecall();
  const since = party.since ? Math.max(0, (Date.now() - Date.parse(party.since)) / 1000) : null;

  const submit = async () => {
    if (!question || !answer.trim()) return;
    setAnswering(true);
    try {
      await answerInput(question.issue_id, question.id, answer.trim(), party.job_id);
      toast.success("Answered. The party carries on.");
      setAnswer("");
      void inputs.refetch();
    } catch (e) {
      toastApiError(e as Error, "Not answered");
    } finally {
      setAnswering(false);
    }
  };

  return (
    <section className="world-slot space-y-2 rounded-sm p-2" data-testid="watch-party">
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <span className="world-subtitle">
          {party.working ? `${party.working} is working` : party.state === "waiting" ? "Waiting for you" : "Between steps"}
        </span>
        <span className="world-muted">
          out {formatSeconds(since)} · {isComplete ? "back" : isConnected ? "live" : "connecting"}
        </span>
      </div>
      {party.stuck && <p className="text-xs" style={{ color: "var(--loss)" }}>It has gone quiet for a while. Recall it, or open the job to look closer.</p>}
      {question && (
        <div className="space-y-1.5" data-testid="party-question">
          <p className="world-bubble inline-block max-w-full whitespace-pre-wrap text-xs" style={{ animation: "none" }}>
            {question.question}
          </p>
          <textarea
            aria-label="Your answer"
            className="world-slot w-full p-1.5 text-xs world-text"
            rows={2}
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
          />
          <button type="button" className="world-button" disabled={!canAct || !answer.trim() || answering} onClick={() => void submit()}>
            {answering ? "Answering…" : "Answer"}
          </button>
        </div>
      )}
      <ol className="max-h-56 space-y-0.5 overflow-y-auto font-mono text-[11px] world-text" data-testid="watch-lines" aria-live="polite">
        {lines.length === 0 && <li className="world-muted">Waiting for word from the party…</li>}
        {lines.map((l) => (
          <li key={l.seq} className="truncate">
            › {l.text}
          </li>
        ))}
      </ol>
      <div className="flex flex-wrap gap-1.5">
        <a className="world-button" href={`/jobs/${party.job_id}`}>
          Open the job
        </a>
        {["travelling", "working", "waiting"].includes(party.state) && (
          <button
            type="button"
            className="world-button"
            data-tone="loss"
            disabled={!canAct || recall.isPending}
            onClick={() =>
              recall.mutate(party.job_id, { onSuccess: () => toast.success("Recalled."), onError: (e) => toastApiError(e, "Not recalled") })
            }
          >
            Recall
          </button>
        )}
      </div>
    </section>
  );
}
