import { useState, type ReactNode } from "react";
import { Link } from "react-router";
import { toast } from "sonner";
import { toastApiError } from "@/api/errorToast";
import type { RunWith } from "@/api/runWith";
import type { QuestScroll as Scroll, Rating } from "@/api/world";
import { RunWithControl } from "@/components/runwith/RunWithControl";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Textarea } from "@/components/ui/Textarea";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { useApproveMerge, useQuest, useRateQuest, useRejectQuest, useSendBack } from "@/hooks/useWorld";
import { providerLabel } from "@/lib/status";
import { formatDateTime } from "@/lib/utils";
import { OUTCOME_WORDS, formatCost, formatSeconds, outcomeBadgeTone, placeOf } from "@/lib/world";

const RATINGS: { value: Rating; label: string }[] = [
  { value: "good", label: "Good" },
  { value: "needs_work", label: "Needs work" },
  { value: "bad", label: "Bad" },
];

interface QuestScrollProps {
  jobId: string;
  skillName: (stat: string) => string;
  canAct: boolean;
  onClose: () => void;
}

/**
 * A finished job's report: what it did, the pull request, the XP, the cost
 * and time, and why it failed if it did. Below, the review: merge it, send it
 * back with notes, reject it, and rate it.
 */
export function QuestScrollDialog({ jobId, skillName, canAct, onClose }: QuestScrollProps) {
  const quest = useQuest(jobId);
  return (
    <Dialog onClose={onClose} title={quest.data?.title ?? "Job report"} className="max-w-2xl">
      {quest.isError ? (
        <p className="p-5 text-sm text-fg-secondary">This report did not load.</p>
      ) : !quest.data ? (
        <p className="p-5 text-sm text-fg-muted">Loading…</p>
      ) : (
        <ScrollBody quest={quest.data} skillName={skillName} canAct={canAct} onClose={onClose} />
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

function ScrollBody({ quest, skillName, canAct, onClose }: { quest: Scroll; skillName: (s: string) => string; canAct: boolean; onClose: () => void }) {
  const lookup = useIdentityLookup();
  const xpTotal = quest.xp.reduce((sum, g) => sum + g.xp, 0);
  return (
    <div className="space-y-5 p-5" data-testid="quest-scroll" data-outcome={quest.outcome}>
      <div className="flex flex-wrap items-center gap-2 text-xs text-fg-muted" data-testid="quest-meta">
        <Badge tone={outcomeBadgeTone(quest.outcome)}>{OUTCOME_WORDS[quest.outcome]}</Badge>
        <span className="font-mono">{placeOf(quest.repo, quest.area)}</span>
        <span>· {formatCost(quest.cost_usd)}</span>
        <span>· {formatSeconds(quest.seconds)}</span>
        <Link className="ml-auto text-fg-muted underline hover:text-fg" to={`/jobs/${quest.job_id}`}>
          Open the job
        </Link>
      </div>

      {quest.failure && (
        <div className="space-y-1 rounded-md border border-destructive/40 bg-red-dim p-3 text-sm" data-testid="quest-failure">
          <p className="text-fg">
            <span className="font-semibold">What went wrong:</span> {quest.failure}.
          </p>
          {quest.error_code && <p className="font-mono text-2xs text-fg-muted">{quest.error_code}</p>}
        </div>
      )}

      {quest.account_wait && (
        <div className="space-y-1 rounded-md border border-border bg-canvas p-3 text-sm" data-testid="quest-account-wait">
          <p className="text-fg">
            <span className="font-semibold">Waiting:</span>{" "}
            {quest.account_wait.reason === "local_busy"
              ? "every slot of the local model is taken"
              : `the ${providerLabel(quest.account_wait.provider)} account is ${quest.account_wait.reason.replace(/_/g, " ")}`}
            . It goes on at {formatDateTime(quest.account_wait.until)}, or sooner once the account is ready again.
          </p>
          {quest.account_wait.chosen && (
            <p className="text-xs text-fg-muted">A person chose this model or key for the quest, so it does not move to another one.</p>
          )}
        </div>
      )}

      <Section title="Report">
        {quest.report ? (
          <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-md border border-border bg-canvas p-3 font-sans text-xs text-fg-secondary" data-testid="quest-report">
            {quest.report}
            {quest.report_truncated ? "\n…" : ""}
          </pre>
        ) : (
          <p className="text-xs text-fg-muted">No report.</p>
        )}
      </Section>

      <Section title="Pull request">
        {quest.pr_url ? (
          <p className="flex flex-wrap items-center gap-1.5 text-xs text-fg-secondary" data-testid="quest-loot">
            <a className="text-fg underline hover:text-primary" href={quest.pr_url} target="_blank" rel="noreferrer">
              Pull request{quest.pr ? ` #${quest.pr.number}` : ""}
            </a>
            {quest.pr && (
              <>
                {" "}
                · {quest.pr.state} · {quest.pr.files ?? "?"} files · <span className="text-success">+{quest.pr.additions ?? "?"}</span>{" "}
                <span className="text-destructive">−{quest.pr.deletions ?? "?"}</span>
              </>
            )}
            {quest.verification?.verdict && <> · checks: {quest.verification.verdict}</>}
            {quest.area && <> · {quest.area}</>}
          </p>
        ) : (
          <p className="text-xs text-fg-muted">No pull request; the report is the result.</p>
        )}
      </Section>

      {quest.xp.length > 0 && (
        <Section title={`XP earned (${xpTotal.toLocaleString("en-US")})`}>
          <ul className="grid grid-cols-1 gap-x-4 gap-y-0.5 text-xs text-fg-secondary sm:grid-cols-2" data-testid="quest-xp">
            {quest.xp.slice(0, 10).map((g) => (
              <li key={`${g.agent_type}-${g.stat}`}>
                {lookup(g.agent_type).displayName}: {skillName(g.stat)} <span className="hud text-success">+{g.xp.toLocaleString("en-US")}</span>
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
                {lookup(p.agent_type).displayName}: {p.runs} run{p.runs === 1 ? "" : "s"}, {formatSeconds(p.seconds)}, {formatCost(p.cost_usd)}
                {p.failed ? ` · ${p.failed} failed` : ""}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {(quest.fallbacks ?? []).length > 0 && (
        <Section title="Fallback models">
          <ul className="space-y-0.5 text-xs text-fg-secondary" data-testid="quest-fallbacks">
            {(quest.fallbacks ?? []).map((f) => (
              <li key={`${f.agent_type}-${f.model}-${f.instead_of}`}>
                {lookup(f.agent_type).displayName} ran on {f.model} instead of {f.instead_of}
                {f.runs > 1 ? ` (${f.runs} runs)` : ""}: the {f.provider ? providerLabel(f.provider) : "provider"}{" "}
                account was paused{f.reason ? ` (${f.reason.replace(/_/g, " ")})` : ""}
                {f.until ? ` until ${formatDateTime(f.until)}` : ""}.
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Judgement quest={quest} canAct={canAct} onDone={onClose} />
    </div>
  );
}

/** The review of the work: merge, send back with notes, reject, and a rating. */
function Judgement({ quest, canAct, onDone }: { quest: Scroll; canAct: boolean; onDone: () => void }) {
  const rate = useRateQuest();
  const sendBack = useSendBack();
  const reject = useRejectQuest();
  const merge = useApproveMerge();
  const [mode, setMode] = useState<"none" | "back" | "reject">("none");
  const [note, setNote] = useState(quest.evaluation?.note ?? "");
  const [closePr, setClosePr] = useState(true);
  const [runWith, setRunWith] = useState<RunWith>({});
  const evaluation = quest.evaluation;
  const back = quest.status !== "running" && quest.outcome !== "out";
  const openPr = !!quest.pr_url && quest.outcome !== "merged" && quest.outcome !== "closed";
  const pendingApproval = quest.merge_approval && quest.merge_approval.status === "pending" ? quest.merge_approval : null;

  const fail = (what: string) => (e: Error) => toastApiError(e, what);
  const doMerge = () => {
    if (pendingApproval) {
      merge.mutate(pendingApproval.id, {
        onSuccess: () =>
          rate.mutate({ jobId: quest.job_id, verdict: "merged" }, { onSuccess: () => toast.success("Merged."), onError: fail("Merged, but not recorded") }),
        onError: fail("The merge was refused"),
      });
    } else {
      rate.mutate({ jobId: quest.job_id, verdict: "merged" }, { onSuccess: () => toast.success("Marked as merged."), onError: fail("Not recorded") });
    }
  };

  if (!back) return <p className="border-t border-border pt-4 text-xs text-fg-muted">Review it when it has finished.</p>;
  return (
    <section className="space-y-3 border-t border-border pt-4" data-testid="quest-judgement">
      <h3 className="text-sm font-semibold">Review</h3>
      {evaluation && (
        <p className="text-xs text-fg-secondary" data-testid="quest-evaluation">
          {evaluation.verdict ? `${evaluation.verdict.replace("_", " ")} · ` : ""}
          {evaluation.rating ? `rated ${evaluation.rating.replace("_", " ")}` : "not rated"}
          {evaluation.followup_job_id ? ` · follow-up ${evaluation.followup_job_id}` : ""}
          {evaluation.by ? ` · by ${evaluation.by}` : ""}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {RATINGS.map((r) => (
          <Button
            key={r.value}
            size="sm"
            variant={evaluation?.rating === r.value ? "secondary" : "outline"}
            aria-pressed={evaluation?.rating === r.value}
            disabled={!canAct || rate.isPending}
            onClick={() =>
              rate.mutate({ jobId: quest.job_id, rating: r.value, note: note.trim() || undefined }, { onSuccess: () => toast.success("Rated."), onError: fail("Not rated") })
            }
          >
            {r.label}
          </Button>
        ))}
      </div>
      <Textarea
        aria-label="A note on the work"
        className="min-h-[60px]"
        rows={2}
        placeholder={mode === "back" ? "What does the work still need?" : "A note on the work (optional)"}
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
      <div className="flex flex-wrap gap-2">
        {openPr && (
          <Button size="sm" disabled={!canAct || merge.isPending || rate.isPending} onClick={doMerge}>
            {pendingApproval ? "Merge" : "I merged it"}
          </Button>
        )}
        {openPr && (
          <Button size="sm" variant="outline" disabled={!canAct} onClick={() => setMode(mode === "back" ? "none" : "back")} aria-expanded={mode === "back"}>
            Send back with notes
          </Button>
        )}
        <Button
          size="sm"
          variant="outline"
          className="text-destructive"
          disabled={!canAct}
          onClick={() => setMode(mode === "reject" ? "none" : "reject")}
          aria-expanded={mode === "reject"}
        >
          Reject
        </Button>
      </div>
      {mode === "back" && (
        <div className="space-y-2 rounded-md border border-border p-3" data-testid="send-back">
          <p className="text-xs text-fg-secondary">The same agent picks the work up on its own branch, with your notes.</p>
          <RunWithControl value={runWith} onChange={setRunWith} repo={quest.repo} />
          <Button
            size="sm"
            disabled={!note.trim() || sendBack.isPending}
            onClick={() =>
              sendBack.mutate(
                { jobId: quest.job_id, notes: note.trim(), runWith },
                {
                  onSuccess: () => {
                    toast.success("Sent back.");
                    onDone();
                  },
                  onError: fail("Not sent back"),
                },
              )
            }
          >
            {sendBack.isPending ? "Sending…" : "Send it back"}
          </Button>
        </div>
      )}
      {mode === "reject" && (
        <div className="space-y-2 rounded-md border border-destructive/40 p-3" data-testid="reject">
          {openPr && (
            <label className="flex items-center gap-2 text-xs text-fg-secondary">
              <input type="checkbox" className="accent-primary" checked={closePr} onChange={(e) => setClosePr(e.target.checked)} />
              Close the pull request, with the note as its last comment
            </label>
          )}
          <Button
            size="sm"
            variant="destructive"
            disabled={reject.isPending}
            onClick={() =>
              reject.mutate(
                { jobId: quest.job_id, note: note.trim(), closePr: openPr && closePr },
                {
                  onSuccess: () => {
                    toast.success("Rejected.");
                    onDone();
                  },
                  onError: fail("Not rejected"),
                },
              )
            }
          >
            {reject.isPending ? "Rejecting…" : "Reject the work"}
          </Button>
        </div>
      )}
      {!canAct && <p className="text-xs text-fg-muted">Only an admin can review a job.</p>}
    </section>
  );
}
