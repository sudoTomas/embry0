import { useEffect, useRef, useState, type ReactNode } from "react";
import { clsx } from "clsx";
import { AlchemicalSigil } from "@/components/divine/AlchemicalSigil";
import { prefersReducedMotion } from "@/hooks/useReducedMotion";
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
  const ref = useRef<HTMLSpanElement>(null);
  const said = useRef<string | null>(null);
  const [ring, setRing] = useState(0);

  // A badge that comes to say something else (a polled job going from
  // Running to Completed) rings once, so the change is seen where it
  // happened. The words are read from the DOM because children can be any
  // node, so a new children object with the same words does not ring; the
  // first render only records them. Gold does not ring: it marks what
  // needs a person and never moves for its own sake.
  useEffect(() => {
    const now = `${tone}|${ref.current?.textContent ?? ""}`;
    const before = said.current;
    said.current = now;
    if (before === null || before === now || tone === "gold" || prefersReducedMotion()) return;
    setRing((n) => n + 1);
  }, [tone, children]);

  return (
    <span
      ref={ref}
      title={title}
      className={clsx(
        "relative inline-flex items-center gap-1 whitespace-nowrap rounded px-1.5 py-0.5 text-2xs font-semibold",
        TONE_CLASSES[tone],
        className,
      )}
    >
      {showSigil && <AlchemicalSigil stage={sigil} size={10} />}
      {children}
      {/* The ring: an outline of the badge in its own colour that expands
          and fades once (index.css .motion-ring), removed when it ends. */}
      {ring > 0 && (
        <span
          key={ring}
          aria-hidden
          data-slot="ring"
          className="motion-ring pointer-events-none absolute inset-0 rounded"
          onAnimationEnd={() => setRing(0)}
        />
      )}
    </span>
  );
}
