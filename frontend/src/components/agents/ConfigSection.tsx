import type { ReactNode } from "react";
import { ChevronRight } from "lucide-react";

/**
 * One part of the agent page, as the repo page's Advanced settings are: a
 * native <details> whose summary names the part and says what it holds now,
 * so a closed page still reads as the agent's configuration. Its open state
 * belongs to the page, which opens every part at once or the part a save
 * error names.
 */
export function ConfigSection({
  id,
  title,
  summary,
  open,
  onToggle,
  children,
}: {
  id: string;
  title: string;
  /** What the part holds now, in a line. */
  summary: ReactNode;
  open: boolean;
  onToggle: (open: boolean) => void;
  children: ReactNode;
}) {
  return (
    <details
      id={`agent-section-${id}`}
      data-testid={`agent-section-${id}`}
      open={open}
      onToggle={(e) => onToggle(e.currentTarget.open)}
      className="advanced-section group rounded-lg border border-border"
    >
      <summary className="flex cursor-pointer list-none items-center gap-3 px-4 py-3 [&::-webkit-details-marker]:hidden">
        <ChevronRight
          className="h-4 w-4 shrink-0 text-fg-muted group-open:rotate-90 motion-safe:transition-transform motion-safe:duration-(--motion-base)"
          aria-hidden="true"
        />
        <h2 className="text-base font-semibold text-fg">{title}</h2>
        <span className="min-w-0 flex-1 truncate text-sm text-fg-secondary" data-testid="agent-section-summary">
          {summary}
        </span>
      </summary>
      <div className="border-t border-border px-4 py-4">{children}</div>
    </details>
  );
}
