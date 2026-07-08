import { useEffect, useState } from "react";
import { cn } from "@/lib/utils";

/** Poll-freshness thresholds (the companion dashboard heartbeat idiom): the jobs
 * poll runs every 5s, so ~3 missed polls reads STALE and ~9 reads OFFLINE. */
const STALE_AFTER_MS = 15_000;
const OFFLINE_AFTER_MS = 45_000;

interface HeartbeatBadgeProps {
  /** Epoch ms of the last successful jobs poll; null before the first one. */
  lastUpdatedAt: number | null;
}

/**
 * Board-header freshness badge: "Updated Ns ago" while polls land, STALE
 * once they stop, OFFLINE when they've been gone long enough (or never
 * arrived). Driven purely by the last successful poll timestamp — the badge
 * ticks locally every second so staleness shows even when nothing re-renders
 * the board.
 */
export function HeartbeatBadge({ lastUpdatedAt }: HeartbeatBadgeProps) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const ageMs = lastUpdatedAt != null ? Math.max(0, now - lastUpdatedAt) : null;
  const mode =
    ageMs == null || ageMs >= OFFLINE_AFTER_MS ? "offline" : ageMs >= STALE_AFTER_MS ? "stale" : "fresh";
  const ageSeconds = ageMs != null ? Math.floor(ageMs / 1000) : null;

  return (
    <span data-testid="heartbeat-badge" className="inline-flex items-center gap-1.5 text-[11px]">
      <span
        className={cn(
          "h-1.5 w-1.5 rounded-full",
          mode === "fresh" ? "bg-success" : mode === "stale" ? "bg-warning" : "bg-destructive",
        )}
      />
      {mode === "fresh" && <span className="text-white/40">Updated {ageSeconds}s ago</span>}
      {mode === "stale" && <span className="text-warning">STALE · updated {ageSeconds}s ago</span>}
      {mode === "offline" && <span className="text-destructive">OFFLINE</span>}
    </span>
  );
}
