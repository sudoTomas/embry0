import { cn } from "@/lib/utils";

interface HeartbeatProps {
  label: string;
  className?: string;
}

export function Heartbeat({ label, className }: HeartbeatProps) {
  return (
    <div
      role="status"
      aria-label={label}
      className={cn("inline-flex items-center gap-2", className)}
    >
      <span
        aria-hidden
        className="vitals-pulse inline-block h-2 w-2 rounded-full bg-primary"
      />
      <span className="text-xs uppercase tracking-wider text-muted-foreground">
        {label}
      </span>
    </div>
  );
}
