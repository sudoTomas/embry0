import { useQuery } from "@tanstack/react-query";
import { fetchProgression } from "@/api/progression";

/** The server recomputes progression at most once a minute, so neither does the page. */
export const PROGRESSION_POLL_MS = 60_000;

export function useProgression() {
  return useQuery({
    queryKey: ["progression"],
    queryFn: fetchProgression,
    staleTime: PROGRESSION_POLL_MS,
    refetchInterval: PROGRESSION_POLL_MS,
  });
}
