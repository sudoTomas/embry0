import { useEffect, useState } from "react";
import { toast } from "sonner";
import type { PackCheck, PackPreview } from "@/api/progressionPack";
import { apiErrorMessage } from "@/api/errorDetail";
import { toastApiError } from "@/api/errorToast";
import { AdminButton } from "@/components/auth/AdminButton";
import { PageError } from "@/components/PageError";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { Textarea } from "@/components/ui/Textarea";
import {
  useActivatePackDraft,
  useCheckPack,
  useDeletePackDraft,
  usePack,
  usePackDraft,
  usePreviewPack,
  useResetPack,
  useSavePackDraft,
} from "@/hooks/useProgressionPack";
import { isBusy, isConflict, packProblems, problemLines } from "@/lib/progressionPack";
import { parseUnifiedDiff } from "@/lib/textDiff";
import { TextDiff } from "@/components/documents/TextDiff";
import { formatDateTime } from "@/lib/utils";
import { useIsAdmin } from "@/stores/authStore";
import { PackProblems } from "./PackProblems";
import { PreviewChanges } from "./PreviewChanges";

type Asked = "activate" | "discard" | "reset";

function Warnings({ warnings }: { warnings: string[] }) {
  if (warnings.length === 0) return null;
  return (
    <div className="space-y-1 text-xs text-warning" data-testid="pack-warnings">
      <p className="font-semibold">Worth knowing, though nothing is wrong:</p>
      <ul className="list-disc pl-5 [overflow-wrap:anywhere]">
        {warnings.map((warning, index) => (
          <li key={index}>{warning}</li>
        ))}
      </ul>
    </div>
  );
}

function CheckResult({ result }: { result: PackCheck }) {
  return (
    <section className="space-y-2 rounded-lg border border-border p-3" data-testid="pack-check" aria-label="Check">
      <h3 className="text-sm font-semibold text-fg">The pack is valid</h3>
      <p className="text-sm text-fg-secondary [overflow-wrap:anywhere]">
        {!result.would_change
          ? "It is the text already in effect."
          : result.sections.length === 0
            ? "Against the pack in effect, only comments or layout change."
            : `Against the pack in effect, it changes: ${result.sections.join(", ")}.`}
      </p>
      <Warnings warnings={result.warnings} />
      {result.text_diff && <TextDiff lines={parseUnifiedDiff(result.text_diff)} label="Text changes" />}
    </section>
  );
}

/**
 * The content pack as YAML text. An edit is checked (a dry run: every
 * problem by key, the sections it changes, the text diff), saved as the one
 * draft, previewed (the whole history re-priced under it, before anyone
 * sees it) and activated. Each write names the version it read, so a write
 * over someone else's newer change is refused (409) instead of overwriting
 * it. Only an admin can change anything; everyone else reads.
 */
export function PackEditor() {
  const pack = usePack();
  const draft = usePackDraft();
  const check = useCheckPack();
  const save = useSavePackDraft();
  const discard = useDeletePackDraft();
  const preview = usePreviewPack();
  const activate = useActivatePackDraft();
  const reset = useResetPack();
  const isAdmin = useIsAdmin();

  // null: showing what is stored (the draft, else the pack in effect); a string: the person's edit.
  const [edit, setEdit] = useState<string | null>(null);
  // The last check, of exactly this text; any edit drops it.
  const [checked, setChecked] = useState<{ text: string; result: PackCheck } | null>(null);
  // The last preview, of exactly this draft version.
  const [previewed, setPreviewed] = useState<PackPreview | null>(null);
  const [savedWarnings, setSavedWarnings] = useState<string[]>([]);
  const [rebase, setRebase] = useState(false);
  const [reason, setReason] = useState("");
  const [conflict, setConflict] = useState<string | null>(null);
  const [asked, setAsked] = useState<Asked | null>(null);

  const stored = draft.data?.text ?? pack.data?.text ?? "";
  const text = edit ?? stored;
  const dirty = edit !== null && edit !== stored;
  const draftVersion = draft.data?.version ?? null;
  // Saved over an earlier pack than the one in effect: activating it is refused until it is saved again.
  const stale = draft.data?.stale === true;

  // Once the stored text is the edit (a save landed and was read back), there is no edit any more.
  useEffect(() => {
    if (edit !== null && draft.data && edit === draft.data.text) setEdit(null);
  }, [draft.data, edit]);

  useEffect(() => {
    if (!dirty) return;
    function handleBeforeUnload(e: BeforeUnloadEvent) {
      e.preventDefault();
    }
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [dirty]);

  if (pack.isError || draft.isError) {
    return (
      <PageError
        message="Could not load the content pack"
        onRetry={() => {
          void pack.refetch();
          void draft.refetch();
        }}
      />
    );
  }
  if (!pack.data || draft.data === undefined) return <Skeleton className="h-64 w-full" />;
  const current = pack.data;
  const shownPreview = previewed && previewed.draft.version === draftVersion ? previewed : null;

  const problems =
    packProblems(check.error) ??
    packProblems(save.error) ??
    packProblems(preview.error) ??
    packProblems(activate.error) ??
    (draft.data?.problems ? problemLines(draft.data.problems) : null);
  const busy =
    check.isPending || save.isPending || discard.isPending || preview.isPending || activate.isPending || reset.isPending;

  function resetMutations() {
    check.reset();
    save.reset();
    preview.reset();
    activate.reset();
  }

  function onEdit(value: string) {
    setEdit(value);
    setChecked(null);
    setSavedWarnings([]);
    resetMutations();
  }

  function revert() {
    setEdit(null);
    setChecked(null);
    setConflict(null);
    resetMutations();
  }

  function conflicted(
    error: unknown,
    then = "Your text is still in the editor and the new version has been read: check it again, or Revert to start from it.",
  ): boolean {
    if (!isConflict(error)) return false;
    // The hook has already read the new version in.
    setConflict(`${apiErrorMessage(error)} ${then}`);
    return true;
  }

  function runCheck() {
    const sent = text;
    resetMutations();
    check.mutate(sent, {
      onSuccess: (result) => setChecked({ text: sent, result }),
      onError: (error) => {
        if (!packProblems(error)) toastApiError(error, "Could not check the pack");
      },
    });
  }

  function runSave() {
    resetMutations();
    save.mutate(
      { text, draftVersion: draftVersion ?? 0, rebase, reason },
      {
        onSuccess: (result) => {
          setConflict(null);
          setChecked(null);
          setPreviewed(null);
          setRebase(false);
          setSavedWarnings(result.warnings);
          toast.success("Draft saved. Nothing changes until it is activated.");
        },
        onError: (error) => {
          if (conflicted(error) || packProblems(error)) return;
          toastApiError(error, "Could not save the draft");
        },
      },
    );
  }

  function runPreview() {
    check.reset();
    save.reset();
    preview.reset();
    preview.mutate(undefined, {
      onSuccess: setPreviewed,
      onError: (error) => {
        if (packProblems(error)) return;
        if (isBusy(error)) toast.info("Another preview is being computed. Try again when it is done.");
        else toastApiError(error, "Could not preview the draft");
      },
    });
  }

  function runActivate() {
    if (draftVersion === null) return;
    activate.mutate(
      { draftVersion, reason },
      {
        onSuccess: (result) => {
          setAsked(null);
          setPreviewed(null);
          setConflict(null);
          setReason("");
          toast.success(`Version ${result.version} is in effect. Every page re-prices the history under it.`);
        },
        onError: (error) => {
          setAsked(null);
          if (conflicted(error, "The preview below is computed again against the pack in effect now.")) {
            // The pack in effect or the draft changed since the preview: show what activating would do now.
            setPreviewed(null);
            runPreview();
            return;
          }
          if (packProblems(error)) return;
          toastApiError(error, "Could not activate the draft");
        },
      },
    );
  }

  function runDiscard() {
    if (draftVersion === null) return;
    discard.mutate(draftVersion, {
      onSuccess: () => {
        setAsked(null);
        setEdit(null);
        setPreviewed(null);
        toast.success("Draft discarded.");
      },
      onError: (error) => {
        setAsked(null);
        if (!conflicted(error)) toastApiError(error, "Could not discard the draft");
      },
    });
  }

  function runReset() {
    reset.mutate(current.version, {
      onSuccess: (result) => {
        setAsked(null);
        toast.success(result.changed ? "The deployment follows the shipped default again." : "Nothing changed.");
      },
      onError: (error) => {
        setAsked(null);
        if (!conflicted(error)) toastApiError(error, "Could not reset the pack");
      },
    });
  }

  return (
    <div className="space-y-3">
      <div className="space-y-0.5 text-xs text-fg-muted [overflow-wrap:anywhere]" data-testid="pack-meta">
        <p>
          {current.version === 0
            ? "Nothing is stored: the deployment follows the shipped default."
            : current.follows_default
              ? `Version ${current.version} follows the shipped default.`
              : `Version ${current.version} is a customised pack` +
                (current.created_by ? `, activated by ${current.created_by}` : "") +
                (current.activated_at ? ` on ${formatDateTime(current.activated_at)}` : "") +
                "."}
        </p>
        <p data-testid="pack-draft-meta">
          {draft.data
            ? `A draft is saved (version ${draft.data.version}${draft.data.created_by ? `, by ${draft.data.created_by}` : ""}): the editor shows it. ` +
              (draft.data.sections.length > 0
                ? `It changes ${draft.data.sections.join(", ")}.`
                : "It changes nothing but comments or layout.")
            : "No draft: the editor shows the pack in effect."}
        </p>
      </div>

      <Textarea
        value={text}
        onChange={(e) => onEdit(e.target.value)}
        readOnly={!isAdmin}
        spellCheck={false}
        // Indentation is the structure of YAML: a long line scrolls inside the box rather than wrap to column 0.
        wrap="off"
        aria-label="Content pack (YAML)"
        className="min-h-[360px] font-mono text-sm"
        data-testid="pack-textarea"
      />
      {!isAdmin && <p className="text-xs text-fg-muted">Only an admin can change the content pack.</p>}

      {conflict && (
        <div role="alert" data-testid="pack-conflict" className="rounded-lg border border-warning/30 bg-amber-dim p-3 text-sm text-warning [overflow-wrap:anywhere]">
          {conflict}
        </div>
      )}
      {stale && draft.data && (
        <p className="rounded-lg border border-warning/30 bg-amber-dim p-3 text-sm text-warning [overflow-wrap:anywhere]" data-testid="pack-draft-stale">
          {`The pack in effect changed since this draft was saved over version ${draft.data.parent_version ?? 0}: version ${current.version} is in effect now. ` +
            "Preview the draft to see what it would change now, carry over what you want to keep, and save it again before you activate it."}
        </p>
      )}
      {problems && (
        <PackProblems
          problems={problems}
          title={
            draft.data?.problems && !check.error && !save.error
              ? "This release refuses the saved draft. Fix these and save it again:"
              : undefined
          }
        />
      )}

      {current.default_moved && isAdmin && (
        <label className="flex items-center gap-2 text-sm text-fg-secondary">
          <input
            type="checkbox"
            checked={rebase}
            onChange={(e) => setRebase(e.target.checked)}
            data-testid="pack-rebase"
          />
          I have taken in the new default
        </label>
      )}

      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-0 flex-1 basis-56 space-y-1">
          <Label htmlFor="pack-reason">Reason (optional)</Label>
          <Input
            id="pack-reason"
            value={reason}
            maxLength={500}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Why, for the history"
            readOnly={!isAdmin}
          />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2">
        <AdminButton variant="secondary" onClick={runCheck} disabled={busy} data-testid="pack-check-button">
          {check.isPending ? "Checking…" : "Check"}
        </AdminButton>
        <AdminButton onClick={runSave} disabled={busy || (!dirty && !rebase && !stale)} data-testid="pack-save-draft">
          {save.isPending ? "Saving…" : "Save draft"}
        </AdminButton>
        <AdminButton
          variant="secondary"
          onClick={runPreview}
          disabled={busy || draftVersion === null || dirty}
          title={dirty ? "Save the draft first: the preview reads the saved draft" : undefined}
          data-testid="pack-preview-button"
        >
          {preview.isPending ? "Previewing…" : "Preview"}
        </AdminButton>
        <AdminButton
          onClick={() => setAsked("activate")}
          disabled={busy || draftVersion === null || dirty || stale}
          title={stale ? "Save the draft again over the pack in effect first" : undefined}
          data-testid="pack-activate"
        >
          Activate
        </AdminButton>
        <Button variant="outline" onClick={revert} disabled={edit === null || busy} data-testid="pack-revert">
          Revert
        </Button>
        <AdminButton
          variant="outline"
          onClick={() => setAsked("discard")}
          disabled={busy || draftVersion === null}
          data-testid="pack-discard"
        >
          Discard draft
        </AdminButton>
        <AdminButton
          variant="outline"
          onClick={() => setAsked("reset")}
          disabled={busy || current.follows_default}
          data-testid="pack-reset"
        >
          Reset to default
        </AdminButton>
      </div>

      <Warnings warnings={savedWarnings} />
      {checked && checked.text === text && <CheckResult result={checked.result} />}
      {shownPreview && (
        <section className="space-y-2 rounded-lg border border-border p-3" aria-label="Preview">
          <h3 className="text-sm font-semibold text-fg">{`What activating draft version ${shownPreview.draft.version} would change`}</h3>
          <PreviewChanges preview={shownPreview} />
        </section>
      )}

      {asked === "activate" && (
        <ConfirmDialog
          title="Activate the draft?"
          confirmLabel="Activate"
          pending={activate.isPending}
          onConfirm={runActivate}
          onClose={() => setAsked(null)}
        >
          {`Draft version ${draftVersion} becomes the pack in effect, and every page re-prices the whole history under it. The pack in effect now stays in the history, so you can restore it.`}
        </ConfirmDialog>
      )}
      {asked === "discard" && (
        <ConfirmDialog
          title="Discard the draft?"
          confirmLabel="Discard"
          destructive
          pending={discard.isPending}
          onConfirm={runDiscard}
          onClose={() => setAsked(null)}
        >
          The draft is deleted and is not kept in the history. The pack in effect does not change.
        </ConfirmDialog>
      )}
      {asked === "reset" && (
        <ConfirmDialog
          title="Reset to the shipped default?"
          confirmLabel="Reset"
          destructive
          pending={reset.isPending}
          onConfirm={runReset}
          onClose={() => setAsked(null)}
        >
          The deployment follows the shipped default again, as each release ships it, and every page re-prices the
          history under it. Your pack stays in the history, so you can restore it.
        </ConfirmDialog>
      )}
    </div>
  );
}
