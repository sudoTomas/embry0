import { useMemo } from "react";
import { useQuery, useQueries, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchJobInputs, answerInput } from "@/api/inputs";
import { isTerminalStatus } from "@/lib/jobStatus";
import { hasAnswered, type QueryAnswers } from "@/hooks/useFirstPaint";

/** One job's questions: the key and fetch every reader of them shares. */
function jobInputsQuery(jobId: string) {
  return { queryKey: ["job-inputs", jobId], queryFn: () => fetchJobInputs(jobId) };
}

/** jobStatus, when known, stops the poll once the job is finished: its
 * questions can no longer change. */
export function useJobInputs(jobId: string, jobStatus?: string) {
  return useQuery({
    ...jobInputsQuery(jobId),
    enabled: !!jobId,
    refetchInterval: isTerminalStatus(jobStatus) ? false : 5000,
  });
}

const allAnswered = (results: QueryAnswers[]) => results.every(hasAnswered);

/**
 * Whether each of these jobs' questions has had its first answer. It starts
 * the fetches when nothing else has, so a view can wait on them before it
 * draws the cards that show them; it polls nothing itself, the cards do. It
 * re-renders only when the answer flips.
 */
export function useJobInputsAnswered(jobIds: readonly string[]): boolean {
  const queries = useMemo(() => jobIds.map((jobId) => jobInputsQuery(jobId)), [jobIds]);
  return useQueries({ queries, combine: allAnswered });
}

export interface AnswerInputVars {
  /** The question's issue, or null for a job created without one. */
  issueId: string | null;
  jobId: string;
  inputId: string;
  answer: string;
}

/**
 * The one way the job surfaces (Job page, Console, logs page) answer an
 * agent's question. jobId is always passed, so an issue-less job answers
 * through the job route instead of throwing in answerInput.
 */
export function useAnswerInput() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ issueId, jobId, inputId, answer }: AnswerInputVars) =>
      answerInput(issueId, inputId, answer, jobId),
    onSuccess: (_, { jobId, issueId }) => {
      qc.invalidateQueries({ queryKey: ["job-inputs", jobId] });
      if (issueId) qc.invalidateQueries({ queryKey: ["issues", issueId, "inputs"] });
    },
  });
}
