import type { DiffLine, DiffLineKind } from "@/lib/settingsDocument";
import { cn } from "@/lib/utils";

const LINE_CLASS: Record<DiffLineKind, string> = {
  add: "bg-teal-dim text-success",
  remove: "bg-red-dim text-destructive",
  same: "text-fg-secondary",
  gap: "text-fg-muted",
  note: "italic text-fg-muted",
};

/** The mark a line starts with; read out too, so the change is not told by colour alone. */
const MARK: Record<DiffLineKind, string> = { add: "+", remove: "-", same: " ", gap: "…", note: " " };

/**
 * A line diff, old lines in red and new ones in green. Long lines wrap
 * rather than scroll, so a phone never scrolls sideways.
 */
export function TextDiff({ lines, label }: { lines: DiffLine[]; label: string }) {
  if (lines.length === 0) return <p className="text-sm text-fg-muted">The text is the same.</p>;
  return (
    <pre
      aria-label={label}
      data-testid="text-diff"
      className="max-h-96 overflow-y-auto whitespace-pre-wrap break-all rounded-lg border border-border bg-overlay p-2 font-mono text-xs"
    >
      {lines.map((line, index) => (
        // The mark keeps its own column, so a wrapped line continues under its text.
        <div key={index} data-kind={line.kind} className={cn("flex px-1", LINE_CLASS[line.kind])}>
          <span className="w-4 shrink-0 select-none">{MARK[line.kind]}</span>
          <span className="min-w-0 flex-1">{line.text}</span>
        </div>
      ))}
    </pre>
  );
}
