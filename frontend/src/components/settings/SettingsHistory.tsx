import { useState } from "react";
import { toast } from "sonner";
import { AdminButton } from "@/components/auth/AdminButton";
import { Button } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { toastApiError } from "@/api/errorToast";
import {
  useSettingsDocument,
  useSettingsRevision,
  useSettingsRevisions,
  useSettingsWriteAccess,
  useUndoSettingsChange,
} from "@/hooks/useSettingsDocument";
import { REVISION_SOURCE_LABELS } from "@/lib/settingsDocument";
import { DEPLOYMENT_LAYER, type PolicyLayer } from "@/lib/settingsLayer";
import { diffLines } from "@/lib/textDiff";
import { TextDiff } from "@/components/documents/TextDiff";
import type { SettingsRevision, SettingsUndoResult } from "@/lib/types/settings";
import { actorLabel } from "@/lib/actors";
import { formatDateTime } from "@/lib/utils";
import { LayersRefusedList } from "./LayersRefusedList";
import { SettingsChangeList } from "./SettingsChangeList";

const UNDO_CONFIRM = "Put back how it was before this change? This is recorded too, so you can undo it again.";

/** One change opened: each key it changed, and the text diff. Read only when opened. */
function RevisionDetail({ id }: { id: number }) {
  const detail = useSettingsRevision(id);
  if (detail.isError) return <p className="px-3 pb-3 text-sm text-destructive">Could not load this change.</p>;
  if (!detail.data) return <Skeleton className="mx-3 mb-3 h-16" />;
  const { changes, before_text: before, after_text: after } = detail.data;
  return (
    <div className="space-y-2 px-3 pb-3" data-testid="settings-revision-detail">
      {changes.length > 0 ? (
        <SettingsChangeList changes={changes} />
      ) : (
        <p className="text-sm text-fg-muted">No setting changed; only comments or layout.</p>
      )}
      <TextDiff lines={diffLines(before ?? "", after ?? "")} label="Text changes" />
    </div>
  );
}

/**
 * Every change to one document, the deployment's or a layer's, newest
 * first. Each can be opened to show what it changed. Only the newest can be
 * undone, while the stored text is still what it wrote, by whoever may
 * write the document; the server says why any other cannot. An undo that
 * makes layer values under the document read as inherited says which. A
 * document kept in a file (`managed_by: file`) is undone from the command
 * line, so the file and the stored document are changed by one hand.
 */
export function SettingsHistory({ layer = DEPLOYMENT_LAYER }: { layer?: PolicyLayer }) {
  const revisions = useSettingsRevisions(layer);
  const undo = useUndoSettingsChange();
  const inFile = useSettingsDocument(layer).data?.managed_by === "file";
  const access = useSettingsWriteAccess(layer);
  const [open, setOpen] = useState<number | null>(null);
  const [undone, setUndone] = useState<SettingsUndoResult | null>(null);

  function onUndo(revision: SettingsRevision) {
    if (undo.isPending || !window.confirm(UNDO_CONFIRM)) return;
    setUndone(null);
    undo.mutate(revision.id, {
      onSuccess: (result) => {
        setUndone(result);
        toast.success("Change undone.");
      },
      onError: (error) => toastApiError(error, "Could not undo the change"),
    });
  }

  if (revisions.isError) {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-sm text-fg-secondary">Could not load the history.</p>
        <Button variant="outline" size="sm" onClick={() => void revisions.refetch()}>
          Retry
        </Button>
      </div>
    );
  }
  if (!revisions.data) return <Skeleton className="h-10 w-full" />;
  if (revisions.data.length === 0) return <p className="text-sm text-fg-muted">No changes recorded yet.</p>;

  return (
    <div className="space-y-3">
      {undone && (
        <LayersRefusedList
          result={undone}
          testId="settings-undo-layers-refused"
          linked
          intro="Undone. These layer values now read as inherited, since they are refused together with the change put back:"
        />
      )}
      <ul className="divide-y divide-border rounded-lg border border-border" data-testid="settings-history">
        {revisions.data.map((revision) => {
          const isOpen = open === revision.id;
          return (
            <li key={revision.id} data-testid="settings-revision">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2">
                {/* A basis, so on a phone the buttons wrap below the words rather than squeeze them. */}
                <div className="min-w-0 flex-1 basis-64">
                  <p className="text-sm text-fg [overflow-wrap:anywhere]">{revision.what}</p>
                  <p className="text-xs text-fg-muted" data-testid="settings-revision-meta">
                    {/* No actor: not known, or not for this key to see. */}
                    {actorLabel(revision.actor)} · {REVISION_SOURCE_LABELS[revision.source] ?? revision.source} ·{" "}
                    {revision.created_at ? formatDateTime(revision.created_at) : "time not known"}
                  </p>
                  {revision.reason && (
                    <p className="text-xs text-fg-secondary [overflow-wrap:anywhere]">{`Reason: ${revision.reason}`}</p>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    variant="ghost"
                    size="xs"
                    aria-expanded={isOpen}
                    onClick={() => setOpen(isOpen ? null : revision.id)}
                  >
                    {isOpen ? "Hide the change" : "Show the change"}
                  </Button>
                  {revision.undoable && inFile ? (
                    <p className="text-xs text-fg-muted" data-testid="settings-undo-in-file">
                      {"Kept in a file: undo it there, or with "}
                      <code className="font-mono">{`embry0 settings undo ${revision.id}`}</code>
                    </p>
                  ) : revision.undoable ? (
                    <AdminButton
                      variant="outline"
                      size="xs"
                      disabled={undo.isPending || !access.canWrite}
                      title={access.reason ?? undefined}
                      onClick={() => onUndo(revision)}
                    >
                      Undo this change
                    </AdminButton>
                  ) : (
                    revision.why_not && <p className="text-xs text-fg-muted">{revision.why_not}</p>
                  )}
                </div>
              </div>
              {isOpen && <RevisionDetail id={revision.id} />}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
