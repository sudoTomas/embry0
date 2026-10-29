import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import {
  activatePackDraft,
  checkPack,
  deletePackDraft,
  fetchDefaultDiff,
  fetchPack,
  fetchPackDraft,
  fetchPackVersion,
  fetchPackVersions,
  previewPackDraft,
  resetPack,
  restorePackVersion,
  savePackDraft,
  type SaveDraftRequest,
} from "@/api/progressionPack";
import { isConflict } from "@/lib/progressionPack";

/** The prefix of every content pack query: the pack, its draft, its history and the default's diff. */
export const PACK_KEY = ["progression-pack"] as const;

const ACTIVE_KEY = [...PACK_KEY, "active"] as const;
const DRAFT_KEY = [...PACK_KEY, "draft"] as const;
const VERSIONS_KEY = [...PACK_KEY, "versions"] as const;
const DEFAULT_DIFF_KEY = [...PACK_KEY, "default-diff"] as const;
/** The app-wide progression read (`useProgression`), which every page's names and colours come from. */
const PROGRESSION_KEY = ["progression"] as const;

/** A write that may change the pack in effect: every pack view, and every page that speaks the pack. */
function refreshAll(qc: QueryClient) {
  return Promise.all([
    qc.invalidateQueries({ queryKey: PACK_KEY }),
    qc.invalidateQueries({ queryKey: PROGRESSION_KEY }),
  ]);
}

/** A draft write: only the pack views (the pack in effect did not change). */
function refreshPack(qc: QueryClient) {
  return qc.invalidateQueries({ queryKey: PACK_KEY });
}

export function usePack() {
  return useQuery({ queryKey: ACTIVE_KEY, queryFn: fetchPack });
}

/**
 * The draft, or null. The pack names its draft (`draft_version`), so the draft is read only when
 * there is one: a deployment without a draft makes no request that answers 404.
 */
export function usePackDraft() {
  const pack = usePack();
  const draftVersion = pack.data?.draft_version ?? null;
  return useQuery({
    queryKey: [...DRAFT_KEY, draftVersion],
    queryFn: () => (draftVersion === null ? Promise.resolve(null) : fetchPackDraft()),
    enabled: pack.isSuccess,
  });
}

export function usePackVersions() {
  return useQuery({ queryKey: VERSIONS_KEY, queryFn: fetchPackVersions });
}

/** One version's text; read only when it is opened. */
export function usePackVersion(version: number | null) {
  return useQuery({
    queryKey: [...VERSIONS_KEY, version],
    queryFn: () => fetchPackVersion(version!),
    enabled: version !== null,
  });
}

/** The diff from the default the pack was taken from to today's; read only when asked for. */
export function useDefaultDiff(enabled: boolean) {
  return useQuery({ queryKey: DEFAULT_DIFF_KEY, queryFn: fetchDefaultDiff, enabled });
}

/** The dry run. It stores nothing, so nothing is refreshed. */
export function useCheckPack() {
  return useMutation({ mutationFn: (text: string) => checkPack(text) });
}

/** The preview reads; it stores nothing either. */
export function usePreviewPack() {
  return useMutation({ mutationFn: () => previewPackDraft() });
}

/** A 409 means someone else changed the pack or its draft: read it again before the caller's own onError runs. */
function onConflict(qc: QueryClient, all: boolean) {
  return (error: unknown) => (isConflict(error) ? (all ? refreshAll(qc) : refreshPack(qc)) : undefined);
}

export function useSavePackDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (request: SaveDraftRequest) => savePackDraft(request),
    onSuccess: () => refreshPack(qc),
    onError: onConflict(qc, false),
  });
}

export function useDeletePackDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (draftVersion: number) => deletePackDraft(draftVersion),
    onSuccess: () => refreshPack(qc),
    onError: onConflict(qc, false),
  });
}

export function useActivatePackDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ draftVersion, reason }: { draftVersion: number; reason?: string }) =>
      activatePackDraft(draftVersion, reason),
    onSuccess: () => refreshAll(qc),
    onError: onConflict(qc, true),
  });
}

export function useRestorePackVersion() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ version, activeVersion }: { version: number; activeVersion: number }) =>
      restorePackVersion(version, activeVersion),
    onSuccess: () => refreshAll(qc),
    onError: onConflict(qc, true),
  });
}

export function useResetPack() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (activeVersion: number) => resetPack(activeVersion),
    onSuccess: () => refreshAll(qc),
    onError: onConflict(qc, true),
  });
}
