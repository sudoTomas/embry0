import { toast } from "sonner";
import { History } from "lucide-react";
import type { HistoryEntry, HistorySource } from "@/api/repoHistory";
import { toastApiError } from "@/api/errorToast";
import { useRepoHistory, useUndoChange } from "@/hooks/useRepoHistory";
import { Button } from "@/components/ui/Button";
import { actorLabel } from "@/lib/actors";
import { formatDateTime } from "@/lib/utils";

const HOW: Record<HistorySource, string> = {
  person: "Dashboard or API",
  bootstrap: "Starter",
  onboard: "embry0 setup",
  proposal: "Setup proposal",
  restore: "Undo",
};

const UNDO_CONFIRM = "Put back how it was before this change? This is recorded too, so you can undo it again.";

/**
 * Every recorded change to this repository's test setup and preferences,
 * newest first. The newest change of each setting can be undone while it
 * is still what is stored; the server says why any other cannot.
 */
export function HistoryPanel({ repo }: { repo: string }) {
  const history = useRepoHistory(repo);
  const undo = useUndoChange(repo);
  const data = history.data;

  function onUndo(entry: HistoryEntry) {
    if (undo.isPending || !window.confirm(UNDO_CONFIRM)) return;
    undo.mutate(entry.id, {
      onSuccess: () => toast.success("Change undone."),
      onError: (error) => toastApiError(error),
    });
  }

  return (
    <section
      id="history"
      className="space-y-2"
      data-testid="history-panel"
      aria-busy={!data && !history.isError}
    >
      <div className="flex items-center gap-2">
        <History className="h-4 w-4 text-fg-muted" />
        <h2 className="text-section font-semibold">History</h2>
      </div>
      {data?.before_history && (
        <p className="text-sm text-muted-foreground">
          Changes made before embry0 kept history are not listed, and who made them is not known.
        </p>
      )}
      {!data ? (
        history.isError ? (
          <div className="flex flex-wrap items-center gap-3">
            <p className="text-sm text-fg-secondary">Could not load the history.</p>
            <Button variant="outline" size="sm" onClick={() => void history.refetch()}>
              Retry
            </Button>
          </div>
        ) : (
          <div className="h-10 w-full motion-safe:animate-pulse rounded bg-muted/60" />
        )
      ) : data.entries.length === 0 ? (
        <p className="text-sm text-muted-foreground">No changes recorded yet.</p>
      ) : (
        <ul className="divide-y divide-border rounded-lg border border-border">
          {data.entries.map((entry) => (
            <li key={entry.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2" data-testid="history-entry">
              <div className="min-w-0 flex-1">
                <p className="text-sm text-fg">{entry.what}</p>
                <p className="text-xs text-fg-muted" data-testid="history-entry-meta">
                  {actorLabel(entry.actor)} · {HOW[entry.source] ?? entry.source} · {formatDateTime(entry.created_at)}
                </p>
              </div>
              {entry.undoable ? (
                <Button variant="outline" size="xs" disabled={undo.isPending} onClick={() => onUndo(entry)}>
                  Undo this change
                </Button>
              ) : (
                entry.why_not && <p className="text-xs text-fg-muted">{entry.why_not}</p>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
