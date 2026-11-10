import { STORED_AS_TEXT_NOTE } from "@/lib/storedAsText";

/** The most of the text shown; the rest is cut, so a large value cannot slow the page. */
const SHOWN_CHARS = 20_000;

/** A stored value the API sent as its JSON text because it is too deeply nested (#431), shown read-only. */
export function StoredAsText({ text, what }: { text: string; what?: string }) {
  const shown = text.length > SHOWN_CHARS ? `${text.slice(0, SHOWN_CHARS)}…` : text;
  return (
    <div className="space-y-1" data-testid="stored-as-text">
      <p className="text-xs text-warning">
        {what ? `${what}: ` : ""}
        {STORED_AS_TEXT_NOTE}
      </p>
      <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-all rounded border border-border bg-overlay p-2 font-mono text-xs text-fg-secondary">
        {shown}
      </pre>
    </div>
  );
}
