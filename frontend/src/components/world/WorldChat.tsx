import type { NarrationLine } from "@/lib/world";

/** How many lines the chat box keeps. */
export const CHAT_LINES = 6;

/** The chat box, bottom left: what the parties did since you opened the hall, newest last. */
export function WorldChat({ lines }: { lines: readonly NarrationLine[] }) {
  return (
    <section className="world-chat rounded-sm p-2 text-xs" aria-label="What happened" aria-live="polite" data-testid="world-chat">
      {lines.length === 0 ? (
        <p className="opacity-70">Welcome to your guild. Parties will report here.</p>
      ) : (
        <ol className="space-y-0.5">
          {lines.slice(-CHAT_LINES).map((l) => (
            <li key={l.key} data-tone={l.tone}>
              {l.text}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
