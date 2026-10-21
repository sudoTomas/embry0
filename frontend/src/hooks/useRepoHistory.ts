import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { fetchHistory, undoChange } from "@/api/repoHistory";
import { toastApiError } from "@/api/errorToast";

/** Refresh everything on the repo page an undo can change. */
function invalidateAfterUndo(qc: QueryClient, fullRepo: string) {
  for (const queryKey of [
    ["repo-history", fullRepo],
    ["qa-config", fullRepo],
    ["repo-preferences", fullRepo],
    ["repos", "detail", fullRepo],
    ["repo-readiness", fullRepo],
  ]) {
    void qc.invalidateQueries({ queryKey });
  }
}

export function useRepoHistory(fullRepo: string) {
  return useQuery({
    queryKey: ["repo-history", fullRepo],
    queryFn: () => fetchHistory(fullRepo),
    enabled: !!fullRepo,
  });
}

export function useUndoChange(fullRepo: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (revisionId: number) => undoChange(fullRepo, revisionId),
    onSuccess: () => invalidateAfterUndo(qc, fullRepo),
  });
}

/**
 * A success toast with an Undo button that reverses the change just made.
 * It takes the caller's query client, so an undo from the toast refreshes
 * the page like one from History. No revision (nothing changed) gives a
 * plain toast.
 */
export function toastWithUndo(message: string, fullRepo: string, revisionId: number | null, qc: QueryClient) {
  if (revisionId === null) {
    toast.success(message);
    return;
  }
  toast.success(message, {
    action: {
      label: "Undo",
      onClick: () => {
        undoChange(fullRepo, revisionId).then(
          () => {
            invalidateAfterUndo(qc, fullRepo);
            toast.success("Change undone.");
          },
          (error: unknown) => toastApiError(error),
        );
      },
    },
  });
}
