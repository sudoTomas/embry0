import type { ReactNode } from "react";

/**
 * An agent's question with its `backtick` spans as inline code. Long
 * identifiers and paths in them may break anywhere, so a question never runs
 * past a narrow card. The caller's element sets the size and colour and must
 * be allowed to shrink (min-w-0) inside a flex row.
 */
export function QuestionText({ text }: { text: string }) {
  const parts: ReactNode[] = text.split(/`([^`\n]+)`/).map((part, i) =>
    i % 2 === 1 ? (
      <code key={i} className="rounded bg-overlay px-1 font-mono text-xs text-fg [overflow-wrap:anywhere]">
        {part}
      </code>
    ) : (
      part
    ),
  );
  return <>{parts}</>;
}
