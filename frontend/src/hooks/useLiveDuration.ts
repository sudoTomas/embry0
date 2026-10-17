import { useEffect, useState } from "react";

/**
 * Wall-clock elapsed for an active card, ticking every second.
 * `cost_update.duration_api_ms` is API time, not wall-clock; the card's
 * elapsed time comes from `agent_started`'s timestamp. Shared by the
 * JobDetailPage AgentCard and the Console board's RunningCard.
 */
export function useLiveDuration(startedAt: string | undefined, isActive: boolean, frozenMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!isActive) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [isActive]);
  if (!isActive) return frozenMs;
  if (!startedAt) return frozenMs;
  return now - new Date(startedAt).getTime();
}
