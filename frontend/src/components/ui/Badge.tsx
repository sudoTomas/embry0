import type { ReactNode } from "react";
import { clsx } from "clsx";
import { AlchemicalSigil } from "@/components/divine/AlchemicalSigil";
import type { Stage } from "@/lib/sigils";

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
  /**
   * Optional alchemical sigil rendered inline before the children.
   * Honors the divine-layer escape hatch (`body[data-divine="off"]`).
   * Skipped on operator-critical tones (error) per divine/CLAUDE.md.
   */
  sigil?: Stage;
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
 * Optional sigil renders inline before children — skipped on `error`
 * (operator-critical paths skip divine flourishes per divine/CLAUDE.md).
 */
export function Badge({ tone = "neutral", children, className, title, sigil }: BadgeProps) {
  const showSigil = sigil && tone !== "error";
  return (
    <span
      title={title}
      className={clsx(
        "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide",
        TONE_CLASSES[tone],
        className,
      )}
    >
      {showSigil && <AlchemicalSigil stage={sigil} size={10} />}
      {children}
    </span>
  );
}
