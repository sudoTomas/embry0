import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { fetchSlopMeter } from "@/api/slopMeter";

/** A repository's Slop-O-Meter and the weekly series of one area ('' the whole repository). */
export function useSlopMeter(repo: string, area: string) {
  return useQuery({
    queryKey: ["repos", "slop-meter", repo, area],
    queryFn: () => fetchSlopMeter(repo, area),
    placeholderData: keepPreviousData,
  });
}
