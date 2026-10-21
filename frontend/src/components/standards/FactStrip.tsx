import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** One line of facts on the raised surface: a summary, not a list of records. */
export function FactStrip({ children, className, ...rest }: { children: ReactNode; className?: string; "data-testid"?: string }) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-5 gap-y-1 rounded-lg border border-border bg-surface px-3 py-2 text-sm",
        className,
      )}
      {...rest}
    >
      {children}
    </div>
  );
}

/** A muted label and its value. */
export function Fact({ label, children, ...rest }: { label: string; children: ReactNode; "data-testid"?: string }) {
  return (
    <span className="inline-flex items-baseline gap-1.5 whitespace-nowrap" {...rest}>
      <span className="text-xs text-fg-muted">{label}</span>
      <span className="text-fg">{children}</span>
    </span>
  );
}

/** A link that stays quiet until pointed at, so the view's one gold stays rare. */
export const QUIET_LINK = "text-sm text-fg-secondary hover:text-primary hover:underline";
