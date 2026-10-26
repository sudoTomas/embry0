import { useState } from "react";
import { parseDoneWhenLines } from "@/lib/doneWhen";
import type { DoneWhen, DoneWhenEdit } from "@/lib/types/jobs";

const SOURCE_WORDS: Record<DoneWhen["source"], string> = {
  triage: "drafted by triage",
  ticket: "from the ticket's acceptance criteria",
  person: "set by a person",
};

/** The separator between a condition and how it is shown, in the editor's lines. */
const SHOWN_SEPARATOR = " — ";

function toLines(doneWhen: DoneWhen | null | undefined): { items: string; scope: string } {
  return {
    items: (doneWhen?.items ?? []).map((i) => (i.shown_by ? `${i.done}${SHOWN_SEPARATOR}${i.shown_by}` : i.done)).join("\n"),
    scope: (doneWhen?.out_of_scope ?? []).join("\n"),
  };
}

/**
 * A job's finish line (done when, spec 2026-10-26): each condition with how
 * it is shown and, once the coding run reported, whether it was met. A person
 * can replace the list while the job is live; the next run works to it.
 */
export function DoneWhenList({
  doneWhen,
  onSave,
}: {
  doneWhen: DoneWhen | null | undefined;
  /** Saves an edit; absent when the list cannot be edited (a finished job). */
  onSave?: (body: DoneWhenEdit) => Promise<unknown>;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(() => toLines(doneWhen));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const checks = new Map((doneWhen?.report?.items ?? []).map((c) => [c.id, c]));
  const items = doneWhen?.items ?? [];
  if (!items.length && !onSave) return null;

  const save = async () => {
    const body = parseDoneWhenLines(draft.items, draft.scope);
    if (!body.items.length) {
      setError("Add at least one condition.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await onSave?.(body);
      setEditing(false);
    } catch {
      setError("Not saved. Try again.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-1.5 text-sm" data-testid="job-done-when">
      <div className="flex items-baseline gap-2">
        <span className="text-fg-muted">Done when</span>
        {doneWhen && (
          <span className="text-xs text-fg-muted">
            {doneWhen.source === "person" && doneWhen.edited_by ? `set by ${doneWhen.edited_by}` : SOURCE_WORDS[doneWhen.source]}
          </span>
        )}
        {onSave && !editing && (
          <button
            type="button"
            className="ml-auto text-xs text-fg-muted underline hover:text-fg"
            onClick={() => {
              setDraft(toLines(doneWhen));
              setEditing(true);
            }}
          >
            {items.length ? "Edit" : "Set a finish line"}
          </button>
        )}
      </div>
      {editing ? (
        <div className="space-y-2" data-testid="done-when-editor">
          <label className="block space-y-1 text-xs text-fg-muted">
            <span>One condition per line, then how it is shown after a dash: condition — how it is shown</span>
            <textarea
              className="h-28 w-full rounded-md border border-border bg-canvas p-2 text-sm text-fg"
              aria-label="Done-when conditions"
              value={draft.items}
              onChange={(e) => setDraft({ ...draft, items: e.target.value })}
            />
          </label>
          <label className="block space-y-1 text-xs text-fg-muted">
            <span>Out of scope, one per line</span>
            <textarea
              className="h-16 w-full rounded-md border border-border bg-canvas p-2 text-sm text-fg"
              aria-label="Out of scope"
              value={draft.scope}
              onChange={(e) => setDraft({ ...draft, scope: e.target.value })}
            />
          </label>
          {error && <p className="text-xs text-destructive">{error}</p>}
          <div className="flex gap-2">
            <button
              type="button"
              className="rounded-md bg-primary px-3 py-1 text-xs font-semibold text-primary-foreground disabled:opacity-50"
              disabled={saving}
              onClick={() => void save()}
            >
              {saving ? "Saving…" : "Save"}
            </button>
            <button type="button" className="text-xs text-fg-muted underline hover:text-fg" onClick={() => setEditing(false)}>
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <>
          <ul className="space-y-1">
            {items.map((item) => {
              const check = checks.get(item.id);
              const mark = check ? (check.met ? "✓" : "✗") : "•";
              const tone = check ? (check.met ? "text-success" : "text-destructive") : "text-fg-muted";
              return (
                <li
                  key={item.id}
                  className="flex gap-2"
                  data-testid="done-when-item"
                  data-met={check ? String(check.met) : undefined}
                >
                  <span className={`shrink-0 ${tone}`} aria-label={check ? (check.met ? "met" : "not met") : "not checked yet"}>
                    {mark}
                  </span>
                  <span className="min-w-0">
                    <span className="text-fg-secondary">{item.done}</span>
                    {item.shown_by && <span className="text-xs text-fg-muted"> — shown by {item.shown_by}</span>}
                    {check?.evidence && <span className="block text-xs text-fg-muted">{check.evidence}</span>}
                  </span>
                </li>
              );
            })}
          </ul>
          {(doneWhen?.out_of_scope ?? []).length > 0 && (
            <details className="text-xs text-fg-muted">
              <summary className="cursor-pointer">Out of scope ({doneWhen?.out_of_scope.length})</summary>
              <ul className="mt-1 list-disc space-y-0.5 pl-4">
                {doneWhen?.out_of_scope.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </div>
  );
}
