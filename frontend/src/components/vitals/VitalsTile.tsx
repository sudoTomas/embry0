import { cn } from "@/lib/utils";

/** A tile that is a status takes that status's colour on its border and value. */
export type TileTone = "neutral" | "running" | "attention" | "good" | "bad";

interface VitalsTileProps {
  label: string;
  value: string;
  /** A second, smaller fact under the label ("7 queued"). */
  detail?: string;
  trend?: string;
  tone?: TileTone;
  className?: string;
}

const TONE: Record<TileTone, { border: string; value: string }> = {
  neutral: { border: "border-border", value: "text-fg" },
  running: { border: "border-info/40", value: "text-info" },
  attention: { border: "border-warning/40", value: "text-warning" },
  good: { border: "border-success/40", value: "text-success" },
  bad: { border: "border-destructive/40", value: "text-destructive" },
};

/** The family stat tile: the value on top, the label under it, sentence case. */
export function VitalsTile({ label, value, detail, trend, tone = "neutral", className }: VitalsTileProps) {
  return (
    <div
      className={cn(
        "h-full rounded-lg border bg-card px-3.5 py-2.5",
        TONE[tone].border,
        className,
      )}
    >
      <p className={cn("truncate font-display text-tile font-bold tracking-tight tabular-nums", TONE[tone].value)}>
        {value}
      </p>
      <p className="mt-1 truncate text-xs text-fg-muted">{label}</p>
      {detail && <p className="truncate text-xs text-fg-secondary">{detail}</p>}
      {trend !== undefined && (
        <p
          data-slot="trend"
          className="mt-1 text-xs text-muted-foreground"
        >
          {trend}
        </p>
      )}
    </div>
  );
}
