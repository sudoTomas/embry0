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
  | "purple"
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
  success: "bg-teal-dim text-success",
  warning: "bg-amber-dim text-warning",
  error: "bg-red-dim text-destructive",
  info: "bg-blue-dim text-info",
  gold: "bg-primary-dim text-primary",
  purple: "bg-purple-dim text-purple",
  neutral: "bg-white/[0.06] text-fg-secondary",
};

/**
 * Status pill primitive: small sentence-case words on a dim wash of the status
 * colour. Pass readable words, never a raw enum: `statusLabel()` in
 * `@/lib/status` maps every status the API returns.
 * Optional sigil renders inline before children — skipped on `error`
 * (operator-critical paths skip divine flourishes per divine/CLAUDE.md).
 */
export function Badge({ tone = "neutral", children, className, title, sigil }: BadgeProps) {
  const showSigil = sigil && tone !== "error";
  return (
    <span
      title={title}
      className={clsx(
        "inline-flex items-center gap-1 whitespace-nowrap rounded px-1.5 py-0.5 text-2xs font-semibold",
        TONE_CLASSES[tone],
        className,
      )}
    >
      {showSigil && <AlchemicalSigil stage={sigil} size={10} />}
      {children}
    </span>
  );
}
