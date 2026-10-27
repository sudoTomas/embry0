import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { type Decision, type DecisionAction, type DecisionFilters, fetchDecision, fetchDecisions } from "@/api/decisions";
import { fetchPeople, returnHandoff, sendDecision, setMyGithubLogin } from "@/api/handoffs";
import { answerOwnerProposal } from "@/api/ownerProposals";
import { type RunWith, runWithBody } from "@/api/runWith";
import {
  approveMerge,
  dismissQuest,
  evaluateQuest,
  rejectQuest,
  runQuestAgain,
  sendQuestBack,
} from "@/api/world";

const KEY = ["decisions"] as const;

/** How long "Later" waits before asking again. */
export const LATER_DAYS = 7;

export function useDecisions(filters: DecisionFilters) {
  return useQuery({ queryKey: [...KEY, filters], queryFn: () => fetchDecisions(filters), refetchInterval: 30_000 });
}

export function useDecision(id: string | null) {
  return useQuery({
    queryKey: [...KEY, "one", id],
    queryFn: () => fetchDecision(id as string),
    enabled: !!id,
    refetchInterval: 30_000,
  });
}

export interface DecisionAnswer {
  decision: Decision;
  action: DecisionAction;
  /** Who answers a quest on offer (recorded with the answer). */
  by: string;
  /** A decline's reason, a send-back's notes or a reject's note. */
  text?: string;
  runWith?: RunWith;
}

/** What one card button does: the route the screen it replaces called, unchanged. */
export async function answerDecision({ decision, action, by, text = "", runWith }: DecisionAnswer): Promise<string> {
  const proposal = decision.links.proposal;
  const job = decision.links.job;
  if (decision.stage === "offered" && proposal) {
    if (action === "start") {
      const done = await answerOwnerProposal(proposal, "accept", { by, ...runWithBody(runWith) });
      return done.job_id ? `Started ${done.job_id}` : "Started";
    }
    if (action === "decline") {
      await answerOwnerProposal(proposal, "decline", { by, reason: text });
      return "Declined";
    }
    if (action === "later") {
      await answerOwnerProposal(proposal, "snooze", { by, until: new Date(Date.now() + LATER_DAYS * 86_400_000).toISOString() });
      return `Asked again in ${LATER_DAYS} days`;
    }
  }
  if (decision.stage === "done" && job) {
    switch (action) {
      case "merge":
        if (decision.links.merge_approval) await approveMerge(decision.links.merge_approval);
        else await evaluateQuest(job, { verdict: "merged" });
        return "Merged";
      case "send_back":
        await sendQuestBack(job, text, runWith);
        return "Sent back";
      case "reject":
        await rejectQuest(job, text, false);
        return "Rejected";
      case "run_again": {
        const again = await runQuestAgain(job, runWith);
        return `Running again as ${again.followup_job_id}`;
      }
      case "good":
      case "bad":
        await evaluateQuest(job, { rating: action });
        return action === "good" ? "Marked good" : "Marked bad";
      case "dismiss":
        await dismissQuest(job);
        return "Dismissed";
    }
  }
  throw new Error(`${action} is not open on this quest`);
}

export function useAnswerDecision() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: answerDecision,
    onSuccess: () => {
      // A card's answer changes what every list of quests shows.
      void qc.invalidateQueries({ queryKey: KEY });
      void qc.invalidateQueries({ queryKey: ["world"] });
      void qc.invalidateQueries({ queryKey: ["owner-proposals"] });
      void qc.invalidateQueries({ queryKey: ["issues"] });
    },
  });
}

/** Who a decision can be sent to: everyone who signs in. */
export function usePeople() {
  return useQuery({ queryKey: ["people"], queryFn: fetchPeople, staleTime: 5 * 60_000 });
}

function useDecisionsMutation<TVars, TResult>(fn: (vars: TVars) => Promise<TResult>, also: string[] = []) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: KEY });
      for (const key of also) void qc.invalidateQueries({ queryKey: [key] });
    },
  });
}

/** Send a decision to a teammate (hand-offs, spec 2026-10-27): it is theirs to decide. */
export function useSendDecision() {
  return useDecisionsMutation((v: { id: string; to: string; note: string }) => sendDecision(v.id, v.to, v.note));
}

/** Not mine to decide: back to whoever sent it. */
export function useReturnHandoff() {
  return useDecisionsMutation((v: { handoffId: string; note: string }) => returnHandoff(v.handoffId, v.note));
}

export function useSetMyGithubLogin() {
  return useDecisionsMutation((login: string) => setMyGithubLogin(login), ["people"]);
}
