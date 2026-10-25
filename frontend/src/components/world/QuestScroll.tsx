import { useState, type ReactNode } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { toastApiError } from "@/api/errorToast";
import type { RunWith } from "@/api/runWith";
import type { QuestScroll as Scroll } from "@/api/world";
import { RunWithControl } from "@/components/runwith/RunWithControl";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Textarea } from "@/components/ui/Textarea";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import {
  useApproveMerge,
  useDismissQuest,
  useQuest,
  useRateQuest,
  useRejectQuest,
  useRunAgain,
  useSendBack,
} from "@/hooks/useWorld";
import { providerLabel } from "@/lib/status";
import { formatDateTime } from "@/lib/utils";
import {
  OUTCOME_WORDS,
  formatCost,
  formatSeconds,
  outcomeBadgeTone,
  placeOf,
  reviewActs,
  type ReviewAct,
} from "@/lib/world";

interface QuestScrollProps {
  jobId: string;
  skillName: (stat: string) => string;
  canAct: boolean;
  onClose: () => void;
  /** After a verdict: the next quest waiting, or close. Defaults to closing. */
  onDone?: () => void;
  /** Leave this one waiting and open the next; absent outside the inbox. */
  onSkip?: () => void;
  /** Where this quest sits in the inbox, e.g. "2 of 5". */
  position?: string;
}

/**
 * A finished job's report: what it did, the pull request, the XP, the cost
 * and time, and why it failed if it did. Below, the review: merge it, send it
 * back with notes, reject it, and rate it.
 */
export function QuestScrollDialog({
  jobId,
  skillName,
  canAct,
  onClose,
  onDone,
  onSkip,
  position,
}: QuestScrollProps) {
  const quest = useQuest(jobId);
  return (
    <Dialog
      onClose={onClose}
      title={quest.data?.title ?? "Job report"}
      className="max-w-2xl"
    >
      {quest.isError ? (
        <p className="p-5 text-sm text-fg-secondary">
          This report did not load.
        </p>
      ) : !quest.data ? (
        <p className="p-5 text-sm text-fg-muted">Loading…</p>
      ) : (
        <ScrollBody
          quest={quest.data}
          skillName={skillName}
          canAct={canAct}
          onDone={onDone ?? onClose}
          onSkip={onSkip}
          position={position}
        />
      )}
    </Dialog>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-1.5">
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  );
}

function ScrollBody({
  quest,
  skillName,
  canAct,
  onDone,
  onSkip,
  position,
}: {
  quest: Scroll;
  skillName: (s: string) => string;
  canAct: boolean;
  onDone: () => void;
  onSkip?: () => void;
  position?: string;
}) {
  const lookup = useIdentityLookup();
  const xpTotal = quest.xp.reduce((sum, g) => sum + g.xp, 0);
  return (
    <div
      className="space-y-5 p-5"
      data-testid="quest-scroll"
      data-outcome={quest.outcome}
    >
      <div
        className="flex flex-wrap items-center gap-2 text-xs text-fg-muted"
        data-testid="quest-meta"
      >
        <Badge tone={outcomeBadgeTone(quest.outcome)}>
          {OUTCOME_WORDS[quest.outcome]}
        </Badge>
        <span className="font-mono">{placeOf(quest.repo, quest.area)}</span>
        <span>· {formatCost(quest.cost_usd)}</span>
        <span>· {formatSeconds(quest.seconds)}</span>
        <Link
          className="ml-auto text-fg-muted underline hover:text-fg"
          to={`/jobs/${quest.job_id}`}
        >
          Open the job
        </Link>
      </div>

      {quest.failure && (
        <div
          className="space-y-1 rounded-md border border-destructive/40 bg-red-dim p-3 text-sm"
          data-testid="quest-failure"
        >
          <p className="text-fg">
            <span className="font-semibold">What went wrong:</span>{" "}
            {quest.failure}.
          </p>
          {quest.error_code && (
            <p className="font-mono text-2xs text-fg-muted">
              {quest.error_code}
            </p>
          )}
        </div>
      )}

      {quest.account_wait && (
        <div
          className="space-y-1 rounded-md border border-border bg-canvas p-3 text-sm"
          data-testid="quest-account-wait"
        >
          <p className="text-fg">
            <span className="font-semibold">Waiting:</span>{" "}
            {quest.account_wait.reason === "local_busy"
              ? "every slot of the local model is taken"
              : `the ${providerLabel(quest.account_wait.provider)} account is ${quest.account_wait.reason.replace(/_/g, " ")}`}
            . It goes on at {formatDateTime(quest.account_wait.until)}, or
            sooner once the account is ready again.
          </p>
          {quest.account_wait.chosen && (
            <p className="text-xs text-fg-muted">
              A person chose this model or key for the quest, so it does not
              move to another one.
            </p>
          )}
        </div>
      )}

      <Section title="Report">
        {quest.report ? (
          <pre
            className="max-h-56 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-canvas p-3 font-sans text-xs text-fg-secondary"
            data-testid="quest-report"
          >
            {quest.report}
            {quest.report_truncated ? "\n…" : ""}
          </pre>
        ) : (
          <p className="text-xs text-fg-muted">No report.</p>
        )}
      </Section>

      <Section title="Pull request">
        {quest.pr_url ? (
          <p
            className="flex flex-wrap items-center gap-1.5 text-xs text-fg-secondary"
            data-testid="quest-loot"
          >
            <a
              className="text-fg underline hover:text-primary"
              href={quest.pr_url}
              target="_blank"
              rel="noreferrer"
            >
              Pull request{quest.pr ? ` #${quest.pr.number}` : ""}
            </a>
            {quest.pr && (
              <>
                {" "}
                · {quest.pr.state} · {quest.pr.files ?? "?"} files ·{" "}
                <span className="text-success">
                  +{quest.pr.additions ?? "?"}
                </span>{" "}
                <span className="text-destructive">
                  −{quest.pr.deletions ?? "?"}
                </span>
              </>
            )}
            {quest.verification?.verdict && (
              <> · checks: {quest.verification.verdict}</>
            )}
            {quest.area && <> · {quest.area}</>}
          </p>
        ) : (
          <p className="text-xs text-fg-muted">
            No pull request; the report is the result.
          </p>
        )}
      </Section>

      {quest.xp.length > 0 && (
        <Section title={`XP earned (${xpTotal.toLocaleString("en-US")})`}>
          <ul
            className="grid grid-cols-1 gap-x-4 gap-y-0.5 text-xs text-fg-secondary sm:grid-cols-2"
            data-testid="quest-xp"
          >
            {quest.xp.slice(0, 10).map((g) => (
              <li key={`${g.agent_type}-${g.stat}`}>
                {lookup(g.agent_type).displayName}: {skillName(g.stat)}{" "}
                <span className="hud text-success">
                  +{g.xp.toLocaleString("en-US")}
                </span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {quest.party.length > 0 && (
        <Section title="Agents">
          <ul className="space-y-0.5 text-xs text-fg-secondary">
            {quest.party.map((p) => (
              <li key={p.agent_type}>
                {lookup(p.agent_type).displayName}: {p.runs} run
                {p.runs === 1 ? "" : "s"}, {formatSeconds(p.seconds)},{" "}
                {formatCost(p.cost_usd)}
                {p.failed ? ` · ${p.failed} failed` : ""}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {(quest.fallbacks ?? []).length > 0 && (
        <Section title="Fallback models">
          <ul
            className="space-y-0.5 text-xs text-fg-secondary"
            data-testid="quest-fallbacks"
          >
            {(quest.fallbacks ?? []).map((f) => (
              <li key={`${f.agent_type}-${f.model}-${f.instead_of}`}>
                {lookup(f.agent_type).displayName} ran on {f.model} instead of{" "}
                {f.instead_of}
                {f.runs > 1 ? ` (${f.runs} runs)` : ""}: the{" "}
                {f.provider ? providerLabel(f.provider) : "provider"} account
                was paused{f.reason ? ` (${f.reason.replace(/_/g, " ")})` : ""}
                {f.until ? ` until ${formatDateTime(f.until)}` : ""}.
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Judgement
        key={quest.job_id}
        quest={quest}
        canAct={canAct}
        onDone={onDone}
        onSkip={onSkip}
        position={position}
      />
    </div>
  );
}

/**
 * The review, pinned to the foot of the report so it never needs a scroll:
 * one click per verdict, and the report moves on to the next quest waiting.
 * Merging rates it good, sending back needs work, rejecting bad. Sending back
 * asks for the notes first; rejecting an open pull request asks once whether
 * to close it.
 */
function Judgement({
  quest,
  canAct,
  onDone,
  onSkip,
  position,
}: {
  quest: Scroll;
  canAct: boolean;
  onDone: () => void;
  onSkip?: () => void;
  position?: string;
}) {
  const rate = useRateQuest();
  const sendBack = useSendBack();
  const reject = useRejectQuest();
  const merge = useApproveMerge();
  const again = useRunAgain();
  const dismiss = useDismissQuest();
  const [ask, setAsk] = useState<"back" | "reject" | null>(null);
  const [note, setNote] = useState(quest.evaluation?.note ?? "");
  const [closePr, setClosePr] = useState(true);
  const [runWith, setRunWith] = useState<RunWith>({});
  const evaluation = quest.evaluation;
  const back = quest.status !== "running" && quest.outcome !== "out";
  const pendingApproval =
    quest.merge_approval && quest.merge_approval.status === "pending"
      ? quest.merge_approval
      : null;
  const busy =
    rate.isPending ||
    sendBack.isPending ||
    reject.isPending ||
    merge.isPending ||
    again.isPending ||
    dismiss.isPending;
  const fail = (what: string) => (e: Error) => toastApiError(e, what);
  const finish = (message: string) => () => {
    toast.success(message);
    onDone();
  };
  const trimmed = note.trim() || undefined;

  if (!back)
    return (
      <p className="border-t border-border pt-4 text-xs text-fg-muted">
        Review it when it has finished.
      </p>
    );

  const run: Record<ReviewAct, () => void> = {
    merge: () => {
      const record = () =>
        rate.mutate(
          {
            jobId: quest.job_id,
            verdict: "merged",
            rating: "good",
            note: trimmed,
          },
          {
            onSuccess: finish(
              pendingApproval ? "Merged." : "Marked as merged.",
            ),
            onError: fail("Not recorded"),
          },
        );
      if (pendingApproval)
        merge.mutate(pendingApproval.id, {
          onSuccess: record,
          onError: fail("The merge was refused"),
        });
      else record();
    },
    good: () =>
      rate.mutate(
        { jobId: quest.job_id, rating: "good", note: trimmed },
        { onSuccess: finish("Rated good."), onError: fail("Not rated") },
      ),
    bad: () =>
      rate.mutate(
        { jobId: quest.job_id, rating: "bad", note: trimmed },
        { onSuccess: finish("Rated bad."), onError: fail("Not rated") },
      ),
    again: () =>
      again.mutate(
        { jobId: quest.job_id, runWith },
        {
          onSuccess: (r) => {
            toast.success(`Running again as ${r.followup_job_id}.`);
            onDone();
          },
          onError: fail("Not started again"),
        },
      ),
    dismiss: () =>
      dismiss.mutate(quest.job_id, {
        onSuccess: finish("Dismissed."),
        onError: fail("Not dismissed"),
      }),
    back: () => {
      if (ask !== "back" || !note.trim()) return setAsk("back");
      sendBack.mutate(
        { jobId: quest.job_id, notes: note.trim(), runWith },
        { onSuccess: finish("Sent back."), onError: fail("Not sent back") },
      );
    },
    reject: () => {
      const openPr = !!quest.pr_url && quest.outcome === "pr_open";
      if (openPr && ask !== "reject") return setAsk("reject");
      reject.mutate(
        { jobId: quest.job_id, note: note.trim(), closePr: openPr && closePr },
        { onSuccess: finish("Rejected."), onError: fail("Not rejected") },
      );
    },
  };
  const LABEL: Record<ReviewAct, string> = {
    merge: pendingApproval ? "Merge" : "I merged it",
    good: "Good",
    bad: "Bad",
    again: "Run again",
    dismiss: "Dismiss",
    back: ask === "back" ? "Send it back" : "Send back",
    reject: ask === "reject" ? "Reject the work" : "Reject",
  };
  const acts = reviewActs(quest);

  return (
    <section
      className="sticky bottom-0 -mx-5 -mb-5 space-y-2 border-t border-border bg-surface px-5 py-3"
      data-testid="quest-judgement"
      aria-label="Review"
    >
      {(ask === "back" || ask === "reject") && (
        <div className="space-y-2">
          <Textarea
            aria-label="A note on the work"
            className="min-h-[56px]"
            rows={2}
            autoFocus
            placeholder={
              ask === "back"
                ? "What does the work still need?"
                : "Why not (optional; it becomes the pull request's last comment)"
            }
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          {ask === "reject" && (
            <label className="flex items-center gap-2 text-xs text-fg-secondary">
              <input
                type="checkbox"
                className="accent-primary"
                checked={closePr}
                onChange={(e) => setClosePr(e.target.checked)}
              />
              Close the pull request
            </label>
          )}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {acts.map((act, i) => (
          <Button
            key={act}
            size="sm"
            variant={
              i === 0 || ask === act
                ? act === "reject" && ask === "reject"
                  ? "destructive"
                  : "default"
                : "outline"
            }
            className={
              act === "reject" && ask !== "reject"
                ? "text-destructive"
                : undefined
            }
            disabled={
              !canAct ||
              busy ||
              (act === "back" && ask === "back" && !note.trim())
            }
            onClick={run[act]}
            data-testid={`review-${act}`}
          >
            {LABEL[act]}
          </Button>
        ))}
        {ask && (
          <Button size="sm" variant="ghost" onClick={() => setAsk(null)}>
            Cancel
          </Button>
        )}
        <span className="ml-auto flex items-center gap-2 text-xs text-fg-muted">
          {position && <span data-testid="review-position">{position}</span>}
          {onSkip && (
            <Button
              size="sm"
              variant="ghost"
              onClick={onSkip}
              data-testid="review-skip"
            >
              Skip
            </Button>
          )}
        </span>
      </div>
      {(acts.includes("again") || ask === "back") && (
        <RunWithControl
          value={runWith}
          onChange={setRunWith}
          repo={quest.repo}
        />
      )}
      {evaluation && (
        <p className="text-2xs text-fg-muted" data-testid="quest-evaluation">
          {evaluation.verdict
            ? `${evaluation.verdict.replace("_", " ")} · `
            : ""}
          {evaluation.rating
            ? `rated ${evaluation.rating.replace("_", " ")}`
            : evaluation.followup_job_id
              ? "run again"
              : "dismissed"}
          {evaluation.followup_job_id
            ? ` · follow-up ${evaluation.followup_job_id}`
            : ""}
          {evaluation.by ? ` · by ${evaluation.by}` : ""}
        </p>
      )}
      {!canAct && (
        <p className="text-xs text-fg-muted">Only an admin can review a job.</p>
      )}
    </section>
  );
}
