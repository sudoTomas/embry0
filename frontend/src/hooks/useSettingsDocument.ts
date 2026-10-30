import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { isConflict } from "@/api/errorDetail";
import {
  fetchEffectiveSettings,
  fetchSettings,
  fetchSettingsLayers,
  fetchSettingsRevision,
  fetchSettingsRevisions,
  previewSettings,
  saveSettings,
  undoSettingsChange,
  type SaveSettingsRequest,
} from "@/api/settings";
import { DEPLOYMENT_LAYER, layerKey, writeAccess, type PolicyLayer, type WriteAccess } from "@/lib/settingsLayer";
import { useIsAdmin } from "@/stores/authStore";

/** The prefix of every settings document query: each layer's document, its effective values and its history, and the list of layers. */
export const SETTINGS_KEY = ["settings-document"] as const;

const DOCUMENT_KEY = [...SETTINGS_KEY, "document"] as const;
const EFFECTIVE_KEY = [...SETTINGS_KEY, "effective"] as const;
const REVISIONS_KEY = [...SETTINGS_KEY, "revisions"] as const;
const REVISION_KEY = [...SETTINGS_KEY, "revision"] as const;
const LAYERS_KEY = [...SETTINGS_KEY, "layers"] as const;

/**
 * Every view reads from one write, and a write to one document changes what
 * the layers under it inherit, what is refused in them and which layers are
 * stored: so a write refreshes them all, every layer's.
 */
function refreshAll(qc: QueryClient) {
  return qc.invalidateQueries({ queryKey: SETTINGS_KEY });
}

export function useSettingsDocument(layer: PolicyLayer = DEPLOYMENT_LAYER) {
  return useQuery({ queryKey: [...DOCUMENT_KEY, layerKey(layer)], queryFn: () => fetchSettings(layer) });
}

export function useEffectiveSettings(layer: PolicyLayer = DEPLOYMENT_LAYER) {
  return useQuery({ queryKey: [...EFFECTIVE_KEY, layerKey(layer)], queryFn: () => fetchEffectiveSettings(layer) });
}

export function useSettingsRevisions(layer: PolicyLayer = DEPLOYMENT_LAYER) {
  return useQuery({ queryKey: [...REVISIONS_KEY, layerKey(layer)], queryFn: () => fetchSettingsRevisions(layer) });
}

/**
 * Whether this session may change the layer's document, as the server
 * decides it: an admin, and for a key with a repository allowlist only a
 * repository's layer the list covers (the document says which).
 */
export function useSettingsWriteAccess(layer: PolicyLayer = DEPLOYMENT_LAYER): WriteAccess {
  const isAdmin = useIsAdmin();
  const covers = useSettingsDocument(layer).data?.allowlist_covers;
  return writeAccess(isAdmin, covers, layer);
}

/** Every stored organization and repository layer, for the picker. */
export function useSettingsLayers() {
  return useQuery({ queryKey: LAYERS_KEY, queryFn: fetchSettingsLayers });
}

/** One revision's texts; nothing is read until a revision is opened. Its id names its layer. */
export function useSettingsRevision(id: number | null) {
  return useQuery({
    queryKey: [...REVISION_KEY, id],
    queryFn: () => fetchSettingsRevision(id!),
    enabled: id !== null,
  });
}

/**
 * The dry run. It stores nothing, so a success refreshes nothing; a 409
 * (the document moved since the edit started) refreshes every view, before
 * the caller's own `onError` runs, as a save does.
 */
export function usePreviewSettings(layer: PolicyLayer = DEPLOYMENT_LAYER) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ yamlText, generation }: { yamlText: string; generation: number }) =>
      previewSettings(yamlText, generation, layer),
    onError: (error) => (isConflict(error) ? refreshAll(qc) : undefined),
  });
}

/**
 * Save the document. A success refreshes every view; so does a 409, since it
 * means someone else changed the document, and the refresh finishes before
 * the caller's own `onError` runs.
 */
export function useSaveSettings(layer: PolicyLayer = DEPLOYMENT_LAYER) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (request: SaveSettingsRequest) => saveSettings(request, layer),
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
