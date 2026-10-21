import { cn } from "@/lib/utils";

export type MeterLevel = "ok" | "warn" | "over";

/** Green below 80 percent of max, amber from 80, red at and past 100. A max of zero or less is "ok": there is nothing to measure against. */
// eslint-disable-next-line react-refresh/only-export-components -- a pure threshold function beside the component it grades
export function levelOf(value: number, max: number): MeterLevel {
  if (max <= 0) return "ok";
  const ratio = value / max;
  if (ratio >= 1) return "over";
  if (ratio >= 0.8) return "warn";
  return "ok";
}

const LEVEL_CLASS: Record<MeterLevel, string> = {
  ok: "bg-palette-emerald",
  warn: "bg-palette-amber",
  over: "bg-destructive",
};

export interface MeterProps {
  value: number;
  max: number;
  label: string;
  className?: string;
}

/** A filled bar against a bound (spec 3.9): the budget meter, a progress bar. The bar caps at full; the aria values tell the truth. */
export function Meter({ value, max, label, className }: MeterProps) {
  const level = levelOf(value, max);
  const pct = max > 0 ? Math.min(100, Math.round((value / max) * 100)) : 0;
  // A valuenow past valuemax is invalid for role="meter"; the text says it in words.
  const valueText = level === "over" ? `${value} of ${max}, over` : `${value} of ${max}`;
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={value}
      aria-valuetext={valueText}
      data-level={level}
      className={cn("h-2 overflow-hidden rounded-full bg-muted", className)}
    >
      <div className={cn("h-full", LEVEL_CLASS[level])} style={{ width: `${pct}%` }} />
    </div>
  );
}
