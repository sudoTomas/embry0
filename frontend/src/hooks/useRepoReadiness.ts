import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchReadiness, skipTesting, type Readiness } from "@/api/repoSetup";

/** The cadence useProposals polls at. */
const POLL_MS = 30_000;

/** Poll only while embry0 is setting up testing; every other state changes on a person's click. */
export function readinessRefetchInterval(data: Readiness | undefined): number | false {
  const testing = data?.rows.find((row) => row.key === "testing");
  return testing?.state === "in_progress" ? POLL_MS : false;
}

export function useRepoReadiness(fullRepo: string) {
  return useQuery({
    queryKey: ["repo-readiness", fullRepo],
    queryFn: () => fetchReadiness(fullRepo),
    enabled: !!fullRepo,
    refetchInterval: (query) => readinessRefetchInterval(query.state.data),
  });
}

export function useSkipTesting(fullRepo: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => skipTesting(fullRepo),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["repo-readiness", fullRepo] });
      void qc.invalidateQueries({ queryKey: ["qa-config", fullRepo] });
      void qc.invalidateQueries({ queryKey: ["repos", "detail", fullRepo] });
    },
  });
}
