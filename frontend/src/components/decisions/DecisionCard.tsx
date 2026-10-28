import { type FormEvent, useState } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import type { Decision, DecisionAction, DecisionTone } from "@/api/decisions";
import { toastApiError } from "@/api/errorToast";
import type { RunWith } from "@/api/runWith";
import { Badge, type BadgeTone } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Textarea } from "@/components/ui/Textarea";
import { useAnswerDecision, usePeople, useReturnHandoff, useSendDecision } from "@/hooks/useDecisions";
import { cn, formatDate, formatDateTime } from "@/lib/utils";

const VERDICT_TONE: Record<DecisionTone, BadgeTone> = { good: "success", warn: "warning", bad: "error", info: "info" };
const EDGE: Record<DecisionTone, string> = {
  good: "border-l-success",
  warn: "border-l-warning",
  bad: "border-l-destructive",
  info: "border-l-border",
};
const FACT_TONE: Record<string, string> = {
  good: "text-success",
  warn: "text-warning",
  bad: "text-destructive",
  "": "text-white/85",
};

export const ACTION_LABEL: Record<DecisionAction, string> = {
  start: "Start",
  decline: "Not this",
  later: "Later",
  send_to: "Send to…",
  merge: "Merge",
  send_back: "Send back",
  reject: "Reject",
  run_again: "Run again",
  good: "Good",
  bad: "Bad",
  dismiss: "Dismiss",
};

/** The answers that carry words: what the box asks, and whether it may be left empty. */
const NEEDS_WORDS: Partial<Record<DecisionAction, { ask: string; required: boolean }>> = {
  decline: { ask: "Why not? Kept with the answer.", required: false },
  send_back: { ask: "What should change? The agents read this.", required: true },
  reject: { ask: "Why reject it? Kept with the answer.", required: false },
};

export interface DecisionCardProps {
  decision: Decision;
  /** Whether the viewer may answer; without it the card only informs. */
  canAnswer: boolean;
  by: string;
  runWith?: RunWith;
  /** Actions this screen does not offer yet. */
  hide?: readonly DecisionAction[];
  /** The viewer's email: the person a hand-off is sent to may answer it whatever their tier. */
  me?: string;
}

/** Send to…: pick a teammate who signs in, add a note; the decision is theirs (spec 2026-10-27). */
function SendTo({ decision, me, onDone }: { decision: Decision; me: string; onDone: () => void }) {
  const people = usePeople();
  const send = useSendDecision();
  const [to, setTo] = useState("");
  const [note, setNote] = useState("");
  const choices = (people.data ?? []).filter(
    (p) => p.email.toLowerCase() !== me.toLowerCase() && p.email.toLowerCase() !== decision.assigned_to?.email,
  );
  const submit = (e: FormEvent) => {
    e.preventDefault();
    send.mutate(
      { id: decision.id, to, note: note.trim() },
      {
        onSuccess: () => {
          toast.success(`Sent to ${choices.find((p) => p.email === to)?.name ?? to}`);
          onDone();
        },
        onError: (err: Error) => toastApiError(err, "Not sent"),
      },
    );
  };
  return (
    <form className="mt-2 space-y-2" onSubmit={submit} data-testid="decision-send-to">
      <select
        aria-label="Send to"
        className="h-8 w-full rounded-md border border-border bg-canvas px-2 text-sm"
        value={to}
        onChange={(e) => setTo(e.target.value)}
      >
        <option value="">Who should decide this?</option>
        {choices.map((p) => (
          <option key={p.email} value={p.email}>
            {p.name}
            {p.github_login ? ` (@${p.github_login})` : ""}
          </option>
        ))}
      </select>
      <Textarea
        aria-label="A note for them"
        placeholder="A note for them: why you think it is theirs (optional)"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        rows={2}
      />
      <div className="flex gap-2">
        <Button size="sm" type="submit" disabled={!to || send.isPending}>
          Send
        </Button>
        <Button size="sm" type="button" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

/**
 * One choice waiting on a person (decisions, spec 2026-10-27): the verdict and
 * where it is, the title, the facts that decide it, one sentence of why, what a
 * yes starts, and the answers. The report or evidence folds underneath. The
 * first action is the likely answer.
 */
export function DecisionCard({ decision, canAnswer, by, runWith, hide = [], me = "" }: DecisionCardProps) {
  const answer = useAnswerDecision();
  const giveBack = useReturnHandoff();
  const held = decision.assigned_to;
  const mine = !!held && !!me && held.email === me.toLowerCase();
  // The person a hand-off was sent to decides it, whatever their tier.
  const mayAnswer = canAnswer || mine;
  const [open, setOpen] = useState(false);
  const [asking, setAsking] = useState<DecisionAction | null>(null);
  const [words, setWords] = useState("");
  const actions = decision.actions.filter((a) => !hide.includes(a));
  const { verdict, links } = decision;

  const run = (action: DecisionAction, text = "") =>
    answer.mutate(
      { decision, action, by, text, runWith },
      {
        onSuccess: (said) => {
          toast.success(said);
          setAsking(null);
        },
        onError: (e: Error) => toastApiError(e, `${ACTION_LABEL[action]} failed`),
      },
    );
  const click = (action: DecisionAction) => {
    if (action === "send_to") {
      setAsking(action);
    } else if (NEEDS_WORDS[action]) {
      setAsking(action);
      setWords("");
    } else run(action);
  };
  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (asking) run(asking, words.trim());
  };

  return (
    <article
      className={cn("rounded-md border border-l-2 border-border bg-white/[0.02] p-3", EDGE[verdict.tone])}
      data-testid="decision-card"
      data-stage={decision.stage}
      data-tone={verdict.tone}
    >
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <Badge tone={VERDICT_TONE[verdict.tone]}>{verdict.label}</Badge>
          <span className="text-fg-secondary">{decision.kind_label}</span>
          <span className="truncate font-mono text-white/60">{decision.repo}</span>
          {decision.area && (
            <Badge tone="purple" title="The part of the repository it is about">
              {decision.area}
            </Badge>
          )}
          {decision.held && (
            <Badge tone="neutral" title="Shown here, not asked in a chat">
              held
            </Badge>
          )}
        </div>
        {decision.at && (
          <time className="text-muted-foreground" dateTime={decision.at} title={formatDateTime(decision.at)}>
            {formatDate(decision.at)}
          </time>
        )}
      </div>
      <h3 className="mt-1.5 text-sm font-semibold text-white/95">{decision.title}</h3>
      {held && (
        <p className="mt-1 text-xs text-info" data-testid="decision-assigned">
          {mine ? "Sent to you" : `With ${held.name}`} by {held.from}
          {held.note ? `: “${held.note}”` : ""}
        </p>
      )}
      {decision.facts.length > 0 && (
        <dl className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs" data-testid="decision-facts">
          {decision.facts.map((f) => (
            <div key={f.label} className="flex gap-1">
              <dt className="text-white/45">{f.label}</dt>
              <dd className={FACT_TONE[f.tone] ?? FACT_TONE[""]}>{f.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {decision.why && <p className="mt-1.5 text-sm text-white/80">{decision.why}</p>}
      {decision.yes_starts && (
        <p className="mt-1 text-xs text-white/60">
          <span className="text-white/45">Yes starts: </span>
          {decision.yes_starts}
        </p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {mayAnswer &&
          actions.map((a, i) => (
            <Button
              key={a}
              size="sm"
              variant={i === 0 ? "default" : a === "reject" || a === "bad" ? "outline" : "ghost"}
              disabled={answer.isPending}
              onClick={() => click(a)}
            >
              {ACTION_LABEL[a]}
            </Button>
          ))}
        {mine && held && (
          <Button
            size="sm"
            variant="ghost"
            disabled={giveBack.isPending}
            onClick={() =>
              giveBack.mutate(
                { handoffId: held.handoff_id, note: "" },
                {
                  onSuccess: () => toast.success(`Back with ${held.from}`),
                  onError: (e: Error) => toastApiError(e, "Not sent back"),
                },
              )
            }
          >
            Not mine
          </Button>
        )}
        <span className="ml-auto flex items-center gap-3 text-xs text-white/55">
          {links.pr && (
            <a className="underline hover:text-white" href={links.pr} target="_blank" rel="noreferrer">
              Pull request
            </a>
          )}
          {links.job && (
            <Link className="underline hover:text-white" to={`/jobs/${links.job}`}>
              Job
            </Link>
          )}
          {links.url && (
            <a className="underline hover:text-white" href={links.url} target="_blank" rel="noreferrer">
              Open
            </a>
          )}
          {decision.details && (
            <button type="button" className="underline hover:text-white" aria-expanded={open} onClick={() => setOpen(!open)}>
              {open ? "Hide details" : "Details"}
            </button>
          )}
        </span>
      </div>
      {asking === "send_to" && <SendTo decision={decision} me={me} onDone={() => setAsking(null)} />}
      {asking && asking !== "send_to" && (
        <form className="mt-2 space-y-2" onSubmit={submit} data-testid="decision-words">
          <Textarea
            aria-label={NEEDS_WORDS[asking]?.ask}
            placeholder={NEEDS_WORDS[asking]?.ask}
            value={words}
            onChange={(e) => setWords(e.target.value)}
            rows={2}
            autoFocus
          />
          <div className="flex gap-2">
            <Button size="sm" type="submit" disabled={answer.isPending || (NEEDS_WORDS[asking]?.required && !words.trim())}>
              {ACTION_LABEL[asking]}
            </Button>
            <Button size="sm" type="button" variant="ghost" onClick={() => setAsking(null)}>
              Cancel
            </Button>
          </div>
        </form>
      )}
      {open && (
        <pre className="mt-2 max-h-80 overflow-auto whitespace-pre-wrap text-xs text-white/70" data-testid="decision-details">
          {decision.details}
        </pre>
      )}
    </article>
  );
}

/** A decision's verdict, facts and why with no answers: what a screen with its own buttons opens with. */
export function DecisionSummary({ decision }: { decision: Decision }) {
  return (
    <div className="space-y-1.5" data-testid="decision-summary" data-tone={decision.verdict.tone}>
      <Badge tone={VERDICT_TONE[decision.verdict.tone]}>{decision.verdict.label}</Badge>
      {decision.facts.length > 0 && (
        <dl className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
          {decision.facts.map((f) => (
            <div key={f.label} className="flex gap-1">
              <dt className="text-white/45">{f.label}</dt>
              <dd className={FACT_TONE[f.tone] ?? FACT_TONE[""]}>{f.value}</dd>
            </div>
          ))}
        </dl>
      )}
      {decision.why && <p className="text-sm text-white/80">{decision.why}</p>}
    </div>
  );
}

/** The verdict badge alone, for a small card that has room for nothing else. */
export function VerdictBadge({ decision }: { decision: Decision }) {
  return <Badge tone={VERDICT_TONE[decision.verdict.tone]}>{decision.verdict.label}</Badge>;
}
