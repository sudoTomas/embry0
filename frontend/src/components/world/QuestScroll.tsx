import { useState } from "react";
import { toast } from "sonner";
import { toastApiError } from "@/api/errorToast";
import type { RunWith } from "@/api/runWith";
import type { QuestScroll as Scroll, Rating } from "@/api/world";
import { RunWithControl } from "@/components/runwith/RunWithControl";
import { Dialog } from "@/components/ui/Dialog";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { useApproveMerge, useQuest, useRateQuest, useRejectQuest, useSendBack } from "@/hooks/useWorld";
import { OUTCOME_WORDS, formatCost, formatSeconds, outcomeTone, placeOf } from "@/lib/world";

const RATINGS: { value: Rating; label: string }[] = [
  { value: "good", label: "Good work" },
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
 * A quest that came back, as its scroll: what it did, the loot, the
 * experience, the cost and time, and why it failed if it did. Below, the
 * player's judgement: merge it, send it back with notes, reject it, and rate it.
 */
export function QuestScrollDialog({ jobId, skillName, canAct, onClose }: QuestScrollProps) {
  const quest = useQuest(jobId);
  return (
    <Dialog onClose={onClose} label="Quest scroll" className="world max-w-2xl">
      {quest.isError ? (
        <div className="world-scroll p-4">This quest's scroll did not load.</div>
      ) : !quest.data ? (
        <div className="world-scroll p-4">Unrolling the scroll…</div>
      ) : (
        <ScrollBody quest={quest.data} skillName={skillName} canAct={canAct} onClose={onClose} />
      )}
    </Dialog>
  );
}

function ScrollBody({ quest, skillName, canAct, onClose }: { quest: Scroll; skillName: (s: string) => string; canAct: boolean; onClose: () => void }) {
  const lookup = useIdentityLookup();
  const tone = outcomeTone(quest.outcome);
  const headline = quest.status === "running" || quest.outcome === "out" ? "Quest in progress" : tone === "loss" ? "Quest failed" : "Quest complete!";
  const xpTotal = quest.xp.reduce((sum, g) => sum + g.xp, 0);
  return (
    <div className="world-scroll hud max-h-[85vh] space-y-3 overflow-y-auto p-5" data-testid="quest-scroll" data-outcome={quest.outcome}>
      <div className="text-center">
        <h2 className="scroll-title title-pixel text-xl">
          {headline}
        </h2>
        <p className="text-sm font-semibold">{quest.title}</p>
        <p className="text-xs opacity-75">
          {placeOf(quest.repo, quest.area)} · {OUTCOME_WORDS[quest.outcome]} · {formatCost(quest.cost_usd)} · {formatSeconds(quest.seconds)}
        </p>
      </div>
      <hr className="scroll-rule" />

      {quest.failure && (
        <p className="text-sm" data-testid="quest-failure">
          <strong>What went wrong:</strong> {quest.failure}.
        </p>
      )}

      <section>
        <h3 className="text-sm font-bold">What it did</h3>
        {quest.report ? (
          <pre className="mt-1 max-h-56 overflow-auto whitespace-pre-wrap text-xs" style={{ fontFamily: "inherit" }} data-testid="quest-report">
            {quest.report}
            {quest.report_truncated ? "\n…" : ""}
          </pre>
        ) : (
          <p className="text-xs opacity-75">It brought back no report.</p>
        )}
      </section>

      <section>
        <h3 className="text-sm font-bold">Loot</h3>
        {quest.pr_url ? (
          <p className="text-xs" data-testid="quest-loot">
            <a className="underline" href={quest.pr_url} target="_blank" rel="noreferrer">
              Pull request{quest.pr ? ` #${quest.pr.number}` : ""}
            </a>
            {quest.pr && (
              <>
                {" "}
                · {quest.pr.state} · {quest.pr.files ?? "?"} files · <span style={{ color: "#005a00" }}>+{quest.pr.additions ?? "?"}</span>{" "}
                <span style={{ color: "#8b0000" }}>−{quest.pr.deletions ?? "?"}</span>
              </>
            )}
            {quest.verification?.verdict && <> · checks: {quest.verification.verdict}</>}
            {quest.area && <> · {quest.area}</>}
          </p>
        ) : (
          <p className="text-xs opacity-75">No pull request: the report above is the loot.</p>
        )}
      </section>

      {quest.xp.length > 0 && (
        <section>
          <h3 className="text-sm font-bold">Experience gained ({xpTotal.toLocaleString("en-US")} xp)</h3>
          <ul className="mt-1 grid grid-cols-1 gap-x-4 text-xs sm:grid-cols-2" data-testid="quest-xp">
            {quest.xp.slice(0, 10).map((g) => (
              <li key={`${g.agent_type}-${g.stat}`}>
                {lookup(g.agent_type).displayName}: {skillName(g.stat)} +{g.xp.toLocaleString("en-US")}
              </li>
            ))}
          </ul>
        </section>
      )}

      {quest.party.length > 0 && (
        <section>
          <h3 className="text-sm font-bold">The party</h3>
          <ul className="mt-1 text-xs">
            {quest.party.map((p) => (
              <li key={p.agent_type}>
                {lookup(p.agent_type).displayName}: {p.runs} run{p.runs === 1 ? "" : "s"}, {formatSeconds(p.seconds)}, {formatCost(p.cost_usd)}
                {p.failed ? ` · ${p.failed} failed` : ""}
              </li>
            ))}
          </ul>
        </section>
      )}

      <hr className="scroll-rule" />
      <Judgement quest={quest} canAct={canAct} onDone={onClose} />
    </div>
  );
}

/** The player's judgement of the work: merge, send back with notes, reject, and a rating. */
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
          rate.mutate({ jobId: quest.job_id, verdict: "merged" }, { onSuccess: () => toast.success("Merged. Loot banked."), onError: fail("Merged, but not recorded") }),
        onError: fail("The merge was refused"),
      });
    } else {
      rate.mutate({ jobId: quest.job_id, verdict: "merged" }, { onSuccess: () => toast.success("Marked as merged."), onError: fail("Not recorded") });
    }
  };

  if (!back) return <p className="text-xs opacity-75">Judge it when the party is back.</p>;
  return (
    <section className="space-y-2" data-testid="quest-judgement">
      <h3 className="text-sm font-bold">Your judgement</h3>
      {evaluation && (
        <p className="text-xs" data-testid="quest-evaluation">
          {evaluation.verdict ? `${evaluation.verdict.replace("_", " ")} · ` : ""}
          {evaluation.rating ? `rated ${evaluation.rating.replace("_", " ")}` : "not rated"}
          {evaluation.followup_job_id ? ` · follow-up ${evaluation.followup_job_id}` : ""}
          {evaluation.by ? ` · by ${evaluation.by}` : ""}
        </p>
      )}
      <div className="flex flex-wrap gap-1.5">
        {RATINGS.map((r) => (
          <button
            key={r.value}
            type="button"
            className="world-button"
            data-tone={r.value === "good" ? "win" : r.value === "bad" ? "loss" : undefined}
            aria-pressed={evaluation?.rating === r.value}
            disabled={!canAct || rate.isPending}
            onClick={() =>
              rate.mutate({ jobId: quest.job_id, rating: r.value, note: note.trim() || undefined }, { onSuccess: () => toast.success("Rated."), onError: fail("Not rated") })
            }
          >
            {r.label}
          </button>
        ))}
      </div>
      <textarea
        aria-label="A note on the work"
        className="w-full rounded-sm border border-[#8a6a3a] bg-[#f4ead0] p-1.5 text-xs text-[#2b1d0e]"
        rows={2}
        placeholder={mode === "back" ? "What does the work still need?" : "A note on the work (optional)"}
        value={note}
        onChange={(e) => setNote(e.target.value)}
      />
      <div className="flex flex-wrap gap-1.5">
        {openPr && (
          <button type="button" className="world-button" data-tone="win" disabled={!canAct || merge.isPending || rate.isPending} onClick={doMerge}>
            {pendingApproval ? "Merge" : "I merged it"}
          </button>
        )}
        {openPr && (
          <button type="button" className="world-button" disabled={!canAct} onClick={() => setMode(mode === "back" ? "none" : "back")} aria-expanded={mode === "back"}>
            Send back with notes
          </button>
        )}
        <button type="button" className="world-button" data-tone="loss" disabled={!canAct} onClick={() => setMode(mode === "reject" ? "none" : "reject")} aria-expanded={mode === "reject"}>
          Reject
        </button>
      </div>
      {mode === "back" && (
        <div className="space-y-1.5 rounded-sm border border-[#8a6a3a] p-2" data-testid="send-back">
          <p className="text-xs">The same character picks the work up on its own branch, with your notes.</p>
          <RunWithControl value={runWith} onChange={setRunWith} repo={quest.repo} />
          <button
            type="button"
            className="world-button"
            disabled={!note.trim() || sendBack.isPending}
            onClick={() =>
              sendBack.mutate(
                { jobId: quest.job_id, notes: note.trim(), runWith },
                { onSuccess: () => { toast.success("Sent back. The party sets off again."); onDone(); }, onError: fail("Not sent back") },
              )
            }
          >
            {sendBack.isPending ? "Sending…" : "Send it back"}
          </button>
        </div>
      )}
      {mode === "reject" && (
        <div className="space-y-1.5 rounded-sm border border-[#8a6a3a] p-2" data-testid="reject">
          {openPr && (
            <label className="flex items-center gap-1.5 text-xs">
              <input type="checkbox" checked={closePr} onChange={(e) => setClosePr(e.target.checked)} />
              Close the pull request, with the note as its last comment
            </label>
          )}
          <button
            type="button"
            className="world-button"
            data-tone="loss"
            disabled={reject.isPending}
            onClick={() =>
              reject.mutate(
                { jobId: quest.job_id, note: note.trim(), closePr: openPr && closePr },
                { onSuccess: () => { toast.success("Rejected."); onDone(); }, onError: fail("Not rejected") },
              )
            }
          >
            {reject.isPending ? "Rejecting…" : "Reject the work"}
          </button>
        </div>
      )}
      {!canAct && <p className="text-xs opacity-75">Only an admin can judge a quest.</p>}
    </section>
  );
}
