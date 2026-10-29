import { useEffect, useState } from "react";
import { toast } from "sonner";
import { AdminButton } from "@/components/auth/AdminButton";
import { PageError } from "@/components/PageError";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { Textarea } from "@/components/ui/Textarea";
import { apiErrorMessage, isConflict } from "@/api/errorDetail";
import { toastApiError } from "@/api/errorToast";
import { usePreviewSettings, useSaveSettings, useSettingsDocument } from "@/hooks/useSettingsDocument";
import { documentProblems, unknownParts, type DocumentProblem } from "@/lib/settingsDocument";
import { parseUnifiedDiff } from "@/lib/textDiff";
import { TextDiff } from "@/components/documents/TextDiff";
import type { SettingsPreview } from "@/lib/types/settings";
import { formatDateTime } from "@/lib/utils";
import { useIsAdmin } from "@/stores/authStore";
import { SettingsChangeList } from "./SettingsChangeList";

const PLACEHOLDER = "# Every key is optional; a key you leave out takes its default.\nversion: 1\n";

/** Why the editor only shows a document kept in a file, and how to change it. */
function ManagedByFile() {
  return (
    <p
      className="rounded-lg border border-border bg-surface px-3 py-2 text-sm text-fg-secondary"
      data-testid="settings-managed-by-file"
    >
      This document is kept in a file (<code className="font-mono text-xs">managed_by: file</code>), so it is
      read-only here. Change the file and store it with{" "}
      <code className="font-mono text-xs">embry0 settings apply</code>. To edit it here again, apply the file
      without <code className="font-mono text-xs">managed_by</code>.
    </p>
  );
}

function Problems({ problems }: { problems: DocumentProblem[] }) {
  return (
    <div
      role="alert"
      data-testid="settings-problems"
      className="space-y-1 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive"
    >
      <p className="font-semibold">The document was refused. Fix these, then preview again:</p>
      <ul className="list-disc space-y-0.5 pl-5 [overflow-wrap:anywhere]">
        {problems.map((problem, index) => (
          <li key={index} data-testid="settings-problem">
            {problem.key && <code className="font-mono">{problem.key}</code>}
            {problem.key ? `: ${problem.msg}` : problem.msg}
          </li>
        ))}
      </ul>
    </div>
  );
}

function PreviewResult({ result }: { result: SettingsPreview }) {
  const unknown = unknownParts(result.unknown_sections, result.unknown_keys);
  return (
    <>
      {!result.would_change ? (
        <p className="text-sm text-fg-muted">Nothing would change: this is the text already stored.</p>
      ) : result.changes.length === 0 ? (
        <p className="text-sm text-fg-muted">Only comments or layout change; no setting does.</p>
      ) : (
        <SettingsChangeList changes={result.changes} />
      )}
      {unknown && (
        <p className="text-xs text-warning [overflow-wrap:anywhere]" data-testid="settings-preview-unknown">
          {`Kept as stored, but not read by this release: ${unknown}.`}
        </p>
      )}
      {result.text_diff && <TextDiff lines={parseUnifiedDiff(result.text_diff)} label="Text changes" />}
    </>
  );
}

/**
 * The settings document as YAML text. A change is previewed first (a dry
 * run: each key it changes, and the text diff), then saved. Both name the
 * generation the edit started from, so a change over someone else's newer
 * one is refused (409) instead of overwriting it; the new version is then
 * read in and the person's text kept. A refused document lists every
 * problem by key. Only an admin can preview or save, and nobody can while
 * the document says it is kept in a file (`managed_by: file`).
 */
export function SettingsDocumentEditor() {
  const doc = useSettingsDocument();
  const preview = usePreviewSettings();
  const save = useSaveSettings();
  const isAdmin = useIsAdmin();

  // null: showing the stored text; a string: the person's edit.
  const [draft, setDraft] = useState<string | null>(null);
  // The generation the edit started from; null after a conflict until the next preview.
  const [base, setBase] = useState<number | null>(null);
  // The last preview, of exactly this text against this generation; any edit drops it.
  const [previewed, setPreviewed] = useState<{ text: string; generation: number; result: SettingsPreview } | null>(
    null,
  );
  const [reason, setReason] = useState("");
  const [conflict, setConflict] = useState<string | null>(null);

  const stored = doc.data?.yaml_text ?? "";
  // A document kept in a file is only shown: an edit begun before it said so is not.
  const inFile = doc.data?.managed_by === "file";
  const text = inFile ? stored : (draft ?? stored);
  const dirty = !inFile && draft !== null && draft !== stored;

  // Once the stored text is the draft (a save landed and was read back, or
  // the version read in after a conflict says the same), there is no edit
  // any more, and nothing to reconcile.
  useEffect(() => {
    if (draft !== null && doc.data && draft === doc.data.yaml_text) {
      setDraft(null);
      setBase(null);
      setConflict(null);
    }
  }, [doc.data, draft]);

  useEffect(() => {
    if (!dirty) return;
    function handleBeforeUnload(e: BeforeUnloadEvent) {
      e.preventDefault();
    }
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [dirty]);

  if (doc.isError) return <PageError message="Could not load the settings document" onRetry={() => void doc.refetch()} />;
  if (!doc.data) return <Skeleton className="h-64 w-full" />;
  const current = doc.data;

  const problems = documentProblems(preview.error) ?? documentProblems(save.error);
  const busy = preview.isPending || save.isPending;

  function edit(value: string) {
    if (draft === null) setBase(current.generation);
    setDraft(value);
    setPreviewed(null);
    preview.reset();
    save.reset();
  }

  function revert() {
    setDraft(null);
    setBase(null);
    setPreviewed(null);
    setConflict(null);
    preview.reset();
    save.reset();
  }

  /** The document moved since the edit started. The hook has already read the new version in. */
  function onConflict(error: unknown) {
    setPreviewed(null);
    setBase(null);
    setConflict(apiErrorMessage(error));
  }

  function runPreview() {
    const sent = text;
    // After a conflict there is no base until now: this preview is of the new version.
    const generation = base ?? current.generation;
    save.reset();
    preview.mutate(
      { yamlText: sent, generation },
      {
        onSuccess: (result) => {
          setPreviewed({ text: sent, generation, result });
          setBase(generation);
          setConflict(null);
        },
        onError: (error) => {
          if (isConflict(error)) onConflict(error);
          else if (!documentProblems(error)) toastApiError(error, "Could not preview the change");
        },
      },
    );
  }

  function runSave() {
    if (!previewed) return;
    save.mutate(
      { yamlText: previewed.text, generation: previewed.generation, reason },
      {
        onSuccess: (result) => {
          setPreviewed(null);
          setReason("");
          toast.success(
            result.revision_id === null
              ? "Nothing changed, so nothing was recorded."
              : "Settings saved. Each loop reads them on its next tick.",
          );
        },
        onError: (error) => {
          if (isConflict(error)) {
            onConflict(error);
            return;
          }
          if (documentProblems(error)) {
            setPreviewed(null);
            return;
          }
          toastApiError(error, "Could not save the settings");
        },
      },
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-xs text-fg-muted" data-testid="settings-document-meta">
        {current.generation === 0
          ? "Nothing is stored yet, so every key takes its default."
          : `Generation ${current.generation}` +
            (current.updated_by ? `, last changed by ${current.updated_by}` : "") +
            (current.updated_at ? ` on ${formatDateTime(current.updated_at)}` : "") +
            "."}
      </p>
      {current.sections.length === 0 ? (
        <p className="rounded-lg border border-border bg-surface px-3 py-2 text-sm text-fg-secondary" data-testid="settings-no-sections">
          No feature keeps its policy in this document yet, so it can hold only comments and{" "}
          <code className="font-mono text-xs">version</code>. Each feature that moves in adds a section.
        </p>
      ) : (
        <p className="text-sm text-fg-secondary [overflow-wrap:anywhere]" data-testid="settings-sections">
          {"Sections: "}
          {current.sections.map((name, index) => (
            <span key={name}>
              {index > 0 && ", "}
              <code className="font-mono text-xs">{name}</code>
            </span>
          ))}
        </p>
      )}

      <Textarea
        value={text}
        onChange={(e) => edit(e.target.value)}
        readOnly={!isAdmin || inFile}
        spellCheck={false}
        // Indentation is the structure of YAML: a long line scrolls inside the box rather than wrap to column 0.
        wrap="off"
        placeholder={PLACEHOLDER}
        aria-label="Settings document (YAML)"
        className="min-h-[280px] font-mono text-sm"
        data-testid="settings-textarea"
      />
      {inFile ? (
        <ManagedByFile />
      ) : (
        !isAdmin && <p className="text-xs text-fg-muted">Only an admin can change the settings document.</p>
      )}

      {conflict && (
        <div role="alert" data-testid="settings-conflict" className="rounded-lg border border-warning/30 bg-amber-dim p-3 text-sm text-warning">
          {`${conflict} Your text is still in the editor and the new version has been read: preview again to see what your text changes in it, or Revert to start from it.`}
        </div>
      )}
      {problems && <Problems problems={problems} />}

      {!inFile && (
        <div className="flex flex-wrap items-center gap-2">
          <AdminButton variant="secondary" onClick={runPreview} disabled={!dirty || busy} data-testid="settings-preview-button">
            {preview.isPending ? "Previewing…" : "Preview"}
          </AdminButton>
          <Button variant="outline" onClick={revert} disabled={draft === null || busy} data-testid="settings-revert">
            Revert
          </Button>
        </div>
      )}

      {previewed && !inFile && (
        <section className="space-y-3 rounded-lg border border-border p-3" data-testid="settings-preview" aria-label="Preview">
          <h3 className="text-sm font-semibold text-fg">What saving would change</h3>
          <PreviewResult result={previewed.result} />
          {previewed.result.would_change && (
            <div className="flex flex-wrap items-end gap-2">
              <div className="min-w-0 flex-1 basis-56 space-y-1">
                <Label htmlFor="settings-reason">Reason (optional)</Label>
                <Input
                  id="settings-reason"
                  value={reason}
                  maxLength={500}
                  onChange={(e) => setReason(e.target.value)}
                  placeholder="Why, for the history"
                />
              </div>
              <AdminButton onClick={runSave} disabled={busy} data-testid="settings-save">
                {save.isPending ? "Saving…" : "Save"}
              </AdminButton>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
