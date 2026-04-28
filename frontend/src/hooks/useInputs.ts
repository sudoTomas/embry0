import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { fetchJobInputs, answerInput, rejectInput } from "@/api/inputs";

export function useJobInputs(jobId: string) {
  return useQuery({
    queryKey: ["job-inputs", jobId],
    queryFn: () => fetchJobInputs(jobId),
    enabled: !!jobId,
    refetchInterval: 5000,
  });
}

export function useAnswerInput() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ issueId, inputId, answer }: { issueId: string; jobId: string; inputId: string; answer: string }) =>
      answerInput(issueId, inputId, answer),
    onSuccess: (_, { jobId }) => qc.invalidateQueries({ queryKey: ["job-inputs", jobId] }),
  });
}

export function useRejectInput() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ jobId, inputId, replacementAnswer }: { jobId: string; inputId: string; replacementAnswer: string }) =>
      rejectInput(jobId, inputId, replacementAnswer),
    onSuccess: (_, { jobId }) => qc.invalidateQueries({ queryKey: ["job-inputs", jobId] }),
  });
}
