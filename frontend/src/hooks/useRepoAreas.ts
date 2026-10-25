import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { fetchRepoAreas, startMapAreas } from "@/api/areas";
import type { RunWith } from "@/api/runWith";

const areasKey = (repo: string) => ["repos", "areas", repo] as const;

/** A repository's areas and its newest map; polls while a map runs. */
export function useRepoAreas(repo: string) {
  return useQuery({
    queryKey: areasKey(repo),
    queryFn: () => fetchRepoAreas(repo),
    refetchInterval: (query) => (query.state.data?.latest_job?.live ? 15_000 : false),
  });
}

export function useMapAreas(repo: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runWith?: RunWith) => startMapAreas(repo, runWith),
    onSuccess: () => void qc.invalidateQueries({ queryKey: areasKey(repo) }),
  });
}
