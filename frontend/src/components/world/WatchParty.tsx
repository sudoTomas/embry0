import { useQuery } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { toastApiError } from "@/api/errorToast";
import { answerInput, fetchJobInputs } from "@/api/inputs";
import type { Party } from "@/api/world";
import { Button, buttonVariants } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Textarea";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { useJobEventStream } from "@/hooks/useJobEventStream";
import { useRecall } from "@/hooks/useWorld";
import { accountWaitWords, describeEvent, formatSeconds } from "@/lib/world";

/** How many lines of what the party does the watch keeps. */
export const WATCH_LINES = 14;

/**
 * A running job, live: the step it is on, its last tool calls and words,
 * its question if it asked one (answer it here), and Cancel.
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
    enabled: party.state === "waiting" && !party.account_wait,
    refetchInterval: 10_000,
  });
  const question = inputs.data?.find((i) => i.status === "pending");
  const [answer, setAnswer] = useState("");
  const [answering, setAnswering] = useState(false);
  const recall = useRecall();
  const lookup = useIdentityLookup();
  const since = party.since ? Math.max(0, (Date.now() - Date.parse(party.since)) / 1000) : null;

  const submit = async () => {
    if (!question || !answer.trim()) return;
    setAnswering(true);
    try {
      await answerInput(question.issue_id, question.id, answer.trim(), party.job_id);
      toast.success("Answered.");
      setAnswer("");
      void inputs.refetch();
    } catch (e) {
      toastApiError(e as Error, "Not answered");
    } finally {
      setAnswering(false);
    }
  };

  return (
    <section className="space-y-3 rounded-md border border-border bg-canvas p-3" aria-label="Live" data-testid="watch-party">
      <div className="flex items-baseline justify-between gap-2 text-xs">
        <span className="font-semibold text-fg">
          {party.working
            ? `${lookup(party.working).displayName} is working`
            : party.state === "waiting" && party.account_wait
              ? `Waiting: ${accountWaitWords(party.account_wait)}`
              : party.state === "waiting"
                ? "Waiting for your answer"
                : "Between steps"}
        </span>
        <span className="text-fg-muted">
          running {formatSeconds(since)} · {isComplete ? "finished" : isConnected ? "live" : "connecting"}
        </span>
      </div>
      {party.stuck && <p className="text-xs text-warning">No activity for a while. Cancel it, or open the job to look closer.</p>}
      {question && (
        <div className="space-y-2" data-testid="party-question">
          <p className="whitespace-pre-wrap rounded-md border border-border bg-overlay p-2 text-sm text-fg">{question.question}</p>
          <Textarea aria-label="Your answer" className="min-h-[60px]" rows={2} value={answer} onChange={(e) => setAnswer(e.target.value)} />
          <Button size="sm" disabled={!canAct || !answer.trim() || answering} onClick={() => void submit()}>
            {answering ? "Answering…" : "Answer"}
          </Button>
        </div>
      )}
      <ol className="max-h-56 space-y-0.5 overflow-y-auto font-mono text-2xs text-fg-secondary" data-testid="watch-lines" aria-live="polite">
        {lines.length === 0 && <li className="text-fg-muted">Waiting for the first event…</li>}
        {lines.map((l) => (
          <li key={l.seq} className="truncate">
            › {l.text}
          </li>
        ))}
      </ol>
      <div className="flex flex-wrap gap-2">
        <Link className={buttonVariants({ variant: "outline", size: "sm" })} to={`/jobs/${party.job_id}`}>
          Open the job
        </Link>
        {["travelling", "working", "waiting"].includes(party.state) && (
          <Button
            variant="destructive"
            size="sm"
            disabled={!canAct || recall.isPending}
            onClick={() =>
              recall.mutate(party.job_id, { onSuccess: () => toast.success("Cancelled."), onError: (e) => toastApiError(e, "Not cancelled") })
            }
          >
            Cancel the job
          </Button>
        )}
      </div>
    </section>
  );
}
