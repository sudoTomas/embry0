import { AlchemicalSigil } from "./AlchemicalSigil";
import type { Stage } from "@/lib/sigils";

interface SacredDividerProps {
  /** Optional sigil to render at the divider's center. */
  stage?: Stage;
  className?: string;
}

/**
 * A 1px gold-tinted line punctuated by an optional sigil at center.
 * Use sparingly: page-header section breaks, modal section breaks.
 * Never inside dense lists. See `divine/CLAUDE.md`.
 */
export function SacredDivider({ stage, className }: SacredDividerProps) {
  return (
    <div
      role="separator"
      aria-orientation="horizontal"
      className={`flex items-center gap-3 divine-element ${className ?? ""}`}
    >
      <span className="flex-1 h-px bg-gradient-to-r from-transparent via-primary/30 to-transparent" />
      {stage && (
        <span className="text-primary/60">
          <AlchemicalSigil stage={stage} size={16} />
        </span>
      )}
      <span className="flex-1 h-px bg-gradient-to-r from-primary/30 via-primary/30 to-transparent" />
    </div>
  );
}
