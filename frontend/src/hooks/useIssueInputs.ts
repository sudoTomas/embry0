import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchIssueInputs, answerInput } from "@/api/inputs";

export function useIssueInputs(issueId: string | undefined) {
  return useQuery({
    queryKey: ["issues", issueId, "inputs"],
    queryFn: () => fetchIssueInputs(issueId!),
    enabled: !!issueId,
    refetchInterval: 5_000,
  });
}

export function useAnswerIssueInput() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ issueId, inputId, answer }: { issueId: string; inputId: string; answer: string }) =>
      answerInput(issueId, inputId, answer),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ["issues", vars.issueId, "inputs"] });
      qc.invalidateQueries({ queryKey: ["issues", vars.issueId] });
      qc.invalidateQueries({ queryKey: ["issues"] });
    },
  });
}
