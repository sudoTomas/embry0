import type { ReactNode } from "react";
import { clsx } from "clsx";

export type BadgeTone =
  | "success"
  | "warning"
  | "error"
  | "info"
  | "gold"
  | "neutral";

interface BadgeProps {
  tone?: BadgeTone;
  children: ReactNode;
  className?: string;
  title?: string;
}

const TONE_CLASSES: Record<BadgeTone, string> = {
  success: "bg-success/10 text-success border-success/25",
  warning: "bg-warning/10 text-warning border-warning/25",
  error: "bg-destructive/10 text-destructive border-destructive/25",
  info: "bg-cyan-500/10 text-cyan-300 border-cyan-500/25",
  gold: "bg-primary/10 text-primary border-primary/25",
  neutral: "bg-white/[0.04] text-white/70 border-white/10",
};

/**
 * Status pill primitive. Six tones backed by Athanor's @theme tokens.
 * Use for any "X PASSED / FAILED / PENDING" state badge.
 */
export function Badge({ tone = "neutral", children, className, title }: BadgeProps) {
  return (
    <span
      title={title}
      className={clsx(
        "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
        TONE_CLASSES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}
