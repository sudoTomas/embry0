import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchJobInputs, answerInput } from "@/api/inputs";

export function useJobInputs(jobId: string) {
  return useQuery({
    queryKey: ["job-inputs", jobId],
    queryFn: () => fetchJobInputs(jobId),
    enabled: !!jobId,
    refetchInterval: 5000,
  });
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
