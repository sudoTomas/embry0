import { api } from "./client";

/** Which setting a change touched. */
export type HistorySurface = "qa_config" | "preferences";

/** The path a change came in by. */
export type HistorySource = "person" | "bootstrap" | "onboard" | "proposal" | "restore";

export interface HistoryEntry {
  id: number;
  surface: HistorySurface;
  /** The change in plain words, written by the server. */
  what: string;
  /** Who made it; empty when not known. */
  actor: string;
  source: HistorySource;
  created_at: string;
  /** Only the newest change of a setting, while it is still what is stored. */
  undoable: boolean;
  why_not: string | null;
}

export interface RepoHistory {
  entries: HistoryEntry[];
  /** A test setup exists that was saved before embry0 kept history. */
  before_history: boolean;
}

export interface UndoResult {
  revision_id: number | null;
}

/** Every recorded change to a repo's test setup and preferences, newest first. */
export async function fetchHistory(fullRepo: string, limit = 50): Promise<RepoHistory> {
  const { data } = await api.get<RepoHistory>(`/repos/${fullRepo}/history`, { params: { limit } });
  return data;
}

/**
 * Put a setting back as it was before one change. The undo is recorded as a
 * change of its own. 409 when the setting changed again since; 422 when the
 * earlier version no longer validates. The caller shows either reason, so
 * the global validation toast is suppressed.
 */
export async function undoChange(fullRepo: string, revisionId: number): Promise<UndoResult> {
  const { data } = await api.post<UndoResult>(`/repos/${fullRepo}/history/${revisionId}/undo`, undefined, {
    suppressValidationToast: true,
  });
  return data;
}
