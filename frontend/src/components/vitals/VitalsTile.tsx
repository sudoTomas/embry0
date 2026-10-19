import type { Ref } from "react";
import { cn } from "@/lib/utils";

/** A tile that is a status takes that status's colour on its border and value.
 * `accent` is the page's one gold: the single number to act on (Needs you). */
export type TileTone = "neutral" | "running" | "good" | "bad" | "accent";

interface VitalsTileProps {
  label: string;
  value: string;
  /** A second, smaller fact under the label ("7 queued"). */
  detail?: string;
  trend?: string;
  tone?: TileTone;
  className?: string;
  /** The value line, for a caller that bumps it when the value changes. */
  valueRef?: Ref<HTMLParagraphElement>;
}

const TONE: Record<TileTone, { border: string; value: string }> = {
  neutral: { border: "border-border", value: "text-fg" },
  running: { border: "border-info/40", value: "text-info" },
  good: { border: "border-success/40", value: "text-success" },
  bad: { border: "border-destructive/40", value: "text-destructive" },
  accent: { border: "border-primary/40", value: "text-primary" },
};

/** The family stat tile: the value on top, the label under it, sentence case. */
export function VitalsTile({ label, value, detail, trend, tone = "neutral", className, valueRef }: VitalsTileProps) {
  return (
    <div
      className={cn(
        "h-full rounded-lg border bg-card px-3.5 py-2.5",
        TONE[tone].border,
        className,
      )}
    >
      {/* Scaled from its left edge, where the number starts, so a bump
          (lib/motion's tick) grows it in place instead of sliding it. */}
      <p
        ref={valueRef}
        className={cn("origin-left truncate font-display text-tile font-bold tracking-tight tabular-nums", TONE[tone].value)}
      >
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
