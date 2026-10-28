import { useState } from "react";
import { toast } from "sonner";
import type { PackVersion } from "@/api/progressionPack";
import { toastApiError } from "@/api/errorToast";
import { AdminButton } from "@/components/auth/AdminButton";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Skeleton } from "@/components/ui/PageSkeleton";
import { usePack, usePackVersion, usePackVersions, useRestorePackVersion } from "@/hooks/useProgressionPack";
import { PACK_SOURCE_LABELS, packProblems } from "@/lib/progressionPack";
import { formatDateTime } from "@/lib/utils";
import { PackProblems } from "./PackProblems";

/** One version's text, read only when opened. */
function VersionText({ version }: { version: number }) {
  const detail = usePackVersion(version);
  if (detail.isError) return <p className="px-3 pb-3 text-sm text-destructive">Could not load this version.</p>;
  if (!detail.data) return <Skeleton className="mx-3 mb-3 h-16" />;
  if (detail.data.text === null) {
    return (
      <p className="px-3 pb-3 text-sm text-fg-muted">
        This version followed the shipped default of its release; it holds no text of its own.
      </p>
    );
  }
  return (
    <pre
      data-testid="pack-version-text"
      className="mx-3 mb-3 max-h-96 overflow-y-auto whitespace-pre-wrap break-all rounded-lg border border-border bg-overlay p-2 font-mono text-xs"
    >
      {detail.data.text}
    </pre>
  );
}

/**
 * Every version of the pack, newest first. Restoring an earlier one is the
 * undo: it becomes the pack in effect again, as a new version, so a restore
 * can itself be undone. Only an admin can restore.
 */
export function PackHistory() {
  const versions = usePackVersions();
  const pack = usePack();
  const restore = useRestorePackVersion();
  const [open, setOpen] = useState<number | null>(null);
  const [asked, setAsked] = useState<PackVersion | null>(null);

  const problems = packProblems(restore.error);

  function onRestore() {
    if (!asked || !pack.data) return;
    restore.mutate(
      { version: asked.version, activeVersion: pack.data.version },
      {
        onSuccess: (result) => {
          setAsked(null);
          toast.success(
            result.changed ? `Version ${asked.version} is in effect again, as version ${result.version}.` : "Nothing changed.",
          );
        },
        onError: (error) => {
          setAsked(null);
          if (!packProblems(error)) toastApiError(error, "Could not restore that version");
        },
      },
    );
  }

  if (versions.isError) {
    return (
      <div className="flex flex-wrap items-center gap-3">
        <p className="text-sm text-fg-secondary">Could not load the history.</p>
        <Button variant="outline" size="sm" onClick={() => void versions.refetch()}>
          Retry
        </Button>
      </div>
    );
  }
  if (!versions.data) return <Skeleton className="h-10 w-full" />;
  if (versions.data.length === 0) {
    return (
      <p className="text-sm text-fg-muted">
        No version is stored yet: the deployment follows the shipped default.
      </p>
    );
  }
  // The newest superseded version is the one the last change replaced: restoring it undoes that change.
  const previous = versions.data.find((v) => v.status === "superseded")?.version;

  return (
    <div className="space-y-2">
      {problems && <PackProblems problems={problems} title="That version cannot be restored:" />}
      <ul className="divide-y divide-border rounded-lg border border-border" data-testid="pack-history">
        {versions.data.map((version) => {
          const isOpen = open === version.version;
          return (
            <li key={version.version} data-testid="pack-version">
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2">
                {/* A basis, so on a phone the buttons wrap below the words rather than squeeze them. */}
                <div className="min-w-0 flex-1 basis-64">
                  <p className="text-sm text-fg">
                    {`Version ${version.version}`}
                    {version.status === "active" && <span className="pl-2 text-xs text-success">in effect</span>}
                    {version.follows_default && <span className="pl-2 text-xs text-fg-muted">follows the shipped default</span>}
                  </p>
                  <p className="text-xs text-fg-muted [overflow-wrap:anywhere]" data-testid="pack-version-meta">
                    {version.created_by || "Someone else"} · {PACK_SOURCE_LABELS[version.source] ?? version.source} ·{" "}
                    {version.activated_at ?? version.created_at
                      ? formatDateTime((version.activated_at ?? version.created_at) as string)
                      : "time not known"}
                  </p>
                  {version.reason && (
                    <p className="text-xs text-fg-secondary [overflow-wrap:anywhere]">{`Reason: ${version.reason}`}</p>
                  )}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  {!version.follows_default && (
                    <Button
                      variant="ghost"
                      size="xs"
                      aria-expanded={isOpen}
                      onClick={() => setOpen(isOpen ? null : version.version)}
                    >
                      {isOpen ? "Hide the text" : "Show the text"}
                    </Button>
                  )}
                  {version.status === "superseded" && (
                    <AdminButton
                      variant="outline"
                      size="xs"
                      disabled={restore.isPending || !pack.data}
                      onClick={() => setAsked(version)}
                      data-testid="pack-restore"
                    >
                      {version.version === previous ? "Undo the last change" : "Restore"}
                    </AdminButton>
                  )}
                </div>
              </div>
              {isOpen && <VersionText version={version.version} />}
            </li>
          );
        })}
      </ul>
      {asked && (
        <ConfirmDialog
          title={`Restore version ${asked.version}?`}
          confirmLabel="Restore"
          pending={restore.isPending}
          onConfirm={onRestore}
          onClose={() => setAsked(null)}
        >
          It becomes the pack in effect again, as a new version, and every page re-prices the history under it. The
          pack in effect now stays in the history, so you can restore it too.
        </ConfirmDialog>
      )}
    </div>
  );
}
