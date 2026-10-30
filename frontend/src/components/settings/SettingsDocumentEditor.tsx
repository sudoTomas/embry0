import { useEffect, useState } from "react";
import { toast } from "sonner";
import { AdminButton } from "@/components/auth/AdminButton";
import { PageError } from "@/components/PageError";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { Textarea } from "@/components/ui/Textarea";
import { apiErrorMessage, isConflict, isNotFound } from "@/api/errorDetail";
import { toastApiError } from "@/api/errorToast";
import {
  usePreviewSettings,
  useSaveSettings,
  useSettingsDocument,
  useSettingsWriteAccess,
} from "@/hooks/useSettingsDocument";
import { actorInText } from "@/lib/actors";
import { documentProblems, unknownParts, type DocumentProblem } from "@/lib/settingsDocument";
import { DEPLOYMENT_LAYER, LAYER_NOUNS, layerName, type PolicyLayer } from "@/lib/settingsLayer";
import { parseUnifiedDiff } from "@/lib/textDiff";
import { TextDiff } from "@/components/documents/TextDiff";
import type { SettingsDocument, SettingsPreview, SettingsSaveResult } from "@/lib/types/settings";
import { formatDateTime } from "@/lib/utils";
import { LayersRefusedList } from "./LayersRefusedList";
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

/** The words a layer's own refusals carry beside their key: why the server's `type` refused it. */
function layerRule(problem: DocumentProblem, layer: PolicyLayer): string | null {
  if (layer.layer === "deployment") return null;
  if (problem.type === "not_overridable") return `Not settable by ${LAYER_NOUNS[layer.layer].toLowerCase()}`;
  if (problem.type === "conflicts_with_inherited") return "Conflicts with the inherited values";
  return null;
}

function Problems({ problems, layer }: { problems: DocumentProblem[]; layer: PolicyLayer }) {
  return (
    <div
      role="alert"
      data-testid="settings-problems"
      className="space-y-1 rounded-lg border border-destructive/30 bg-destructive/10 p-3 text-sm text-destructive"
    >
      <p className="font-semibold">The document was refused. Fix these, then preview again:</p>
      <ul className="list-disc space-y-0.5 pl-5 [overflow-wrap:anywhere]">
        {problems.map((problem, index) => {
          const rule = layerRule(problem, layer);
          return (
            <li key={index} data-testid="settings-problem" data-type={problem.type}>
              {problem.key && <code className="font-mono">{problem.key}</code>}
              {rule && (
                <>
                  {" "}
                  <Badge tone="error">{rule}</Badge>
                </>
              )}
              {problem.key ? `: ${problem.msg}` : problem.msg}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Per section, the keys a layer may set; the deployment's document sets every key, so it lists its sections instead. */
function Settable({ doc, layer }: { doc: SettingsDocument; layer: PolicyLayer }) {
  if (layer.layer === "deployment" || !doc.settable) {
    if (doc.sections.length === 0) {
      return (
        <p className="rounded-lg border border-border bg-surface px-3 py-2 text-sm text-fg-secondary" data-testid="settings-no-sections">
          No feature keeps its policy in this document yet, so it can hold only comments and{" "}
          <code className="font-mono text-xs">version</code>. Each feature that moves in adds a section.
        </p>
      );
    }
    return (
      <p className="text-sm text-fg-secondary [overflow-wrap:anywhere]" data-testid="settings-sections">
        {"Sections: "}
        {doc.sections.map((name, index) => (
          <span key={name}>
            {index > 0 && ", "}
            <code className="font-mono text-xs">{name}</code>
          </span>
        ))}
      </p>
    );
  }
  const noun = LAYER_NOUNS[layer.layer];
  const sections = Object.entries(doc.settable).filter(([, keys]) => keys.length > 0);
  if (sections.length === 0) {
    return (
      <p className="rounded-lg border border-border bg-surface px-3 py-2 text-sm text-fg-secondary" data-testid="settings-settable">
        {`${noun} cannot set any key yet: no section lets it. Every value here is inherited.`}
      </p>
    );
  }
  return (
    <div className="space-y-1 text-sm text-fg-secondary" data-testid="settings-settable">
      <p>{`${noun} can set only these keys; every other key is inherited, and setting one is refused:`}</p>
      <ul className="list-disc space-y-0.5 pl-5 [overflow-wrap:anywhere]">
        {sections.map(([section, keys]) => (
          <li key={section} data-testid="settings-settable-section">
            <code className="font-mono text-xs">{section}</code>
            {": "}
            {keys.map((key, index) => (
              <span key={key}>
                {index > 0 && ", "}
                <code className="font-mono text-xs">{key}</code>
              </span>
            ))}
          </li>
        ))}
      </ul>
    </div>
  );
}

/** When the document was written and by whom; a layer nobody wrote says it inherits everything. */
function documentMeta(doc: SettingsDocument, layer: PolicyLayer): string {
  if (doc.generation === 0) {
    return layer.layer === "deployment"
      ? "Nothing is stored yet, so every key takes its default."
      : `${layerName(layer)} has no layer yet, so it inherits every key. Write the keys it sets and save to start one.`;
  }
  return (
    `Generation ${doc.generation}, last changed by ${actorInText(doc.updated_by)}` +
    (doc.updated_at ? ` on ${formatDateTime(doc.updated_at)}` : "") +
    "."
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
      <LayersRefusedList
        result={result}
        testId="settings-preview-layers-refused"
        intro="Saving would make these layer values read as inherited, since they are refused together with this change:"
      />
      {result.text_diff && <TextDiff lines={parseUnifiedDiff(result.text_diff)} label="Text changes" />}
    </>
  );
}

/**
 * One settings document as YAML text: the deployment's, or an organization's
 * or a repository's layer. A change is previewed first (a dry run: each key
 * it changes, the text diff, and the layer values it would make read as
 * inherited), then saved. Both name the generation the edit started from (0
 * for a layer nobody wrote, which the save creates), so a change over
 * someone else's newer one is refused (409) instead of overwriting it; the
 * new version is then read in and the person's text kept. A refused document
 * lists every problem by key, a key the layer may not set marked as such.
 * Only an admin can preview or save, a key with a repository allowlist only
 * a repository's layer it covers, and nobody can while the document says it
 * is kept in a file (`managed_by: file`). `onDirtyChange` hears whether an
 * edit is open, so the page can ask before it is dropped.
 */
export function SettingsDocumentEditor({
  layer = DEPLOYMENT_LAYER,
  onDirtyChange,
}: {
  layer?: PolicyLayer;
  onDirtyChange?: (dirty: boolean) => void;
}) {
  const doc = useSettingsDocument(layer);
  const preview = usePreviewSettings(layer);
  const save = useSaveSettings(layer);
  const access = useSettingsWriteAccess(layer);

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
  // The last save's answer, kept until the next edit: which layer values it made read as inherited.
  const [saved, setSaved] = useState<SettingsSaveResult | null>(null);

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
    onDirtyChange?.(dirty);
  }, [dirty, onDirtyChange]);

  useEffect(() => {
    if (!dirty) return;
    function handleBeforeUnload(e: BeforeUnloadEvent) {
      e.preventDefault();
    }
    window.addEventListener("beforeunload", handleBeforeUnload);
    return () => window.removeEventListener("beforeunload", handleBeforeUnload);
  }, [dirty]);

  if (doc.isError) {
    // A layer of an organization or repository nothing registers: retrying cannot help, the server says what can.
    if (isNotFound(doc.error)) return <PageError message={apiErrorMessage(doc.error)} />;
    return <PageError message="Could not load the settings document" onRetry={() => void doc.refetch()} />;
  }
  if (!doc.data) return <Skeleton className="h-64 w-full" />;
  const current = doc.data;

  const problems = documentProblems(preview.error) ?? documentProblems(save.error);
  const busy = preview.isPending || save.isPending;

  function edit(value: string) {
    if (draft === null) setBase(current.generation);
    setDraft(value);
    setPreviewed(null);
    setSaved(null);
    preview.reset();
    save.reset();
  }

  function revert() {
    setDraft(null);
    setBase(null);
    setPreviewed(null);
    setConflict(null);
    setSaved(null);
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
    setSaved(null);
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
          setSaved(result);
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
        {documentMeta(current, layer)}
      </p>
      <Settable doc={current} layer={layer} />

      <Textarea
        value={text}
        onChange={(e) => edit(e.target.value)}
        readOnly={!access.canWrite || inFile}
        spellCheck={false}
        // Indentation is the structure of YAML: a long line scrolls inside the box rather than wrap to column 0.
        wrap="off"
        placeholder={PLACEHOLDER}
        aria-label={layer.layer === "deployment" ? "Settings document (YAML)" : `${layerName(layer)}'s layer (YAML)`}
        className="min-h-[280px] font-mono text-sm"
        data-testid="settings-textarea"
      />
      {inFile ? (
        <ManagedByFile />
      ) : (
        access.reason && (
          <p className="text-xs text-fg-muted" data-testid="settings-read-only">
            {access.reason}
          </p>
        )
      )}

      {conflict && (
        <div role="alert" data-testid="settings-conflict" className="rounded-lg border border-warning/30 bg-amber-dim p-3 text-sm text-warning">
          {`${conflict} Your text is still in the editor and the new version has been read: preview again to see what your text changes in it, or Revert to start from it.`}
        </div>
      )}
      {problems && <Problems problems={problems} layer={layer} />}

      {!inFile && (
        <div className="flex flex-wrap items-center gap-2">
          <AdminButton
            variant="secondary"
            onClick={runPreview}
            disabled={!dirty || busy || !access.canWrite}
            data-testid="settings-preview-button"
          >
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
              <AdminButton onClick={runSave} disabled={busy || !access.canWrite} data-testid="settings-save">
                {save.isPending ? "Saving…" : "Save"}
              </AdminButton>
            </div>
          )}
        </section>
      )}

      {saved && (
        <LayersRefusedList
          result={saved}
          testId="settings-saved-layers-refused"
          linked
          intro="Saved. These layer values now read as inherited, since they are refused together with this change:"
        />
      )}
    </div>
  );
}
