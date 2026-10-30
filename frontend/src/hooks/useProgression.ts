import { useQuery } from "@tanstack/react-query";
import { fetchProgression } from "@/api/progression";
import { useUiSettings } from "@/hooks/useUiSettings";

/** The server recomputes progression at most once a minute, so neither does the page. */
export const PROGRESSION_POLL_MS = 60_000;

/**
 * The account's progression, polled for every page that shows a level, an
 * outfit or an achievement. While the settings document turns the game off
 * (`ui.game`, #177) nothing is read, and a page still open when it went off
 * forgets what it had read: the key moves to one no query fills, so a level
 * or an outfit never outlives the game.
 */
export function useProgression() {
  const { game } = useUiSettings();
  return useQuery({
    queryKey: game ? ["progression"] : ["progression", "off"],
    queryFn: fetchProgression,
    enabled: game,
    staleTime: PROGRESSION_POLL_MS,
    refetchInterval: PROGRESSION_POLL_MS,
  });
}
