import { cn } from "@/lib/utils";

export type HeartbeatTone = "primary" | "live" | "down" | "waiting";

interface HeartbeatProps {
  label: string;
  /** Dot colour. Only a live (or primary) heartbeat pulses. */
  tone?: HeartbeatTone;
  /** Rail mode: the dot alone, the label stays as the accessible name. */
  hideLabel?: boolean;
  className?: string;
}

const DOT: Record<HeartbeatTone, string> = {
  primary: "bg-primary",
  live: "bg-success",
  down: "bg-destructive",
  waiting: "bg-fg-muted",
};

export function Heartbeat({ label, tone = "primary", hideLabel = false, className }: HeartbeatProps) {
  const pulses = tone === "primary" || tone === "live";
  return (
    <div
      role="status"
      aria-label={label}
      className={cn("inline-flex items-center gap-2", className)}
    >
      <span
        aria-hidden
        className={cn("inline-block h-2 w-2 shrink-0 rounded-full", pulses && "vitals-pulse", DOT[tone])}
      />
      {!hideLabel && <span className="truncate text-xs font-semibold">{label}</span>}
    </div>
  );
}
