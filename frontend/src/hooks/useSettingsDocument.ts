import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { isConflict } from "@/api/errorDetail";
import {
  fetchEffectiveSettings,
  fetchSettings,
  fetchSettingsRevision,
  fetchSettingsRevisions,
  previewSettings,
  saveSettings,
  undoSettingsChange,
  type SaveSettingsRequest,
} from "@/api/settings";

/** The prefix of every settings document query: the document, its effective values and its history. */
export const SETTINGS_KEY = ["settings-document"] as const;

const DOCUMENT_KEY = [...SETTINGS_KEY, "document"] as const;
const EFFECTIVE_KEY = [...SETTINGS_KEY, "effective"] as const;
const REVISIONS_KEY = [...SETTINGS_KEY, "revisions"] as const;

/** Every view of the document reads from one write, so a write refreshes them all. */
function refreshAll(qc: QueryClient) {
  return qc.invalidateQueries({ queryKey: SETTINGS_KEY });
}

export function useSettingsDocument() {
  return useQuery({ queryKey: DOCUMENT_KEY, queryFn: fetchSettings });
}

export function useEffectiveSettings() {
  return useQuery({ queryKey: EFFECTIVE_KEY, queryFn: fetchEffectiveSettings });
}

export function useSettingsRevisions() {
  return useQuery({ queryKey: REVISIONS_KEY, queryFn: () => fetchSettingsRevisions() });
}

/** One revision's texts; nothing is read until a revision is opened. */
export function useSettingsRevision(id: number | null) {
  return useQuery({
    queryKey: [...REVISIONS_KEY, id],
    queryFn: () => fetchSettingsRevision(id!),
    enabled: id !== null,
  });
}

/**
 * The dry run. It stores nothing, so a success refreshes nothing; a 409
 * (the document moved since the edit started) refreshes every view, before
 * the caller's own `onError` runs, as a save does.
 */
export function usePreviewSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ yamlText, generation }: { yamlText: string; generation: number }) =>
      previewSettings(yamlText, generation),
    onError: (error) => (isConflict(error) ? refreshAll(qc) : undefined),
  });
}

/**
 * Save the document. A success refreshes every view; so does a 409, since it
 * means someone else changed the document, and the refresh finishes before
 * the caller's own `onError` runs.
 */
export function useSaveSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (request: SaveSettingsRequest) => saveSettings(request),
    onSuccess: () => refreshAll(qc),
    onError: (error) => (isConflict(error) ? refreshAll(qc) : undefined),
  });
}

/** Undo the newest change. A 409 (it is no longer the newest) refreshes the history too. */
export function useUndoSettingsChange() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (revisionId: number) => undoSettingsChange(revisionId),
    onSuccess: () => refreshAll(qc),
    onError: (error) => (isConflict(error) ? refreshAll(qc) : undefined),
  });
}
