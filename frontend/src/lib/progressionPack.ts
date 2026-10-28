// Pure helpers for the Game content tab: the problems of a refused pack and
// the words for where a version came from. Its diffs use @/lib/textDiff.

import type { PackProblem, PackSource } from "@/api/progressionPack";

/** One problem of a refused pack: the key it belongs to (null for the whole text) and why. */
export interface PackProblemLine {
  key: string | null;
  msg: string;
}

/** A path from the document's root as the pack's own check prints it: `achievements[3].thresholds`. */
export function problemKey(loc: readonly (string | number)[]): string | null {
  let out = "";
  for (const part of loc) {
    out += typeof part === "number" ? `[${part}]` : out ? `.${part}` : String(part);
  }
  return out || null;
}

/** Each problem of a list the API returned, by key. Values are never part of them. */
export function problemLines(problems: readonly PackProblem[]): PackProblemLine[] {
  return problems.flatMap((p) => {
    const msg = typeof p?.msg === "string" ? p.msg.trim().replace(/^Value error, /, "") : "";
    if (!msg) return [];
    return [{ key: Array.isArray(p.loc) ? problemKey(p.loc) : null, msg }];
  });
}

/**
 * The problems of a 422 from a pack write, one per bad key, in the order
 * the server listed them; null for any other failure. The detail is
 * FastAPI's list (`{loc, type, msg}`); `input` is never read.
 */
export function packProblems(error: unknown): PackProblemLine[] | null {
  const response = (error as { response?: { status?: unknown; data?: { detail?: unknown } } } | null)?.response;
  if (response?.status !== 422) return null;
  const detail = response.data?.detail;
  if (typeof detail === "string") return [{ key: null, msg: detail }];
  if (!Array.isArray(detail)) return [{ key: null, msg: "The pack was refused." }];
  return problemLines(detail as PackProblem[]);
}

/** Whether a write was refused because the pack or its draft changed since it was read (HTTP 409). */
export function isConflict(error: unknown): boolean {
  return (error as { response?: { status?: unknown } } | null)?.response?.status === 409;
}

/** Whether a preview was refused because another one is running (HTTP 429). */
export function isBusy(error: unknown): boolean {
  return (error as { response?: { status?: unknown } } | null)?.response?.status === 429;
}

export const PACK_SOURCE_LABELS: Record<PackSource, string> = {
  person: "Dashboard or API",
  file: "Pack file",
  restore: "Undo",
  reset: "Reset to the default",
  upgrade: "Upgrade",
};

/** A signed difference for a before → after cell: `+3`, `−2`, or empty when equal or unknown. */
export function delta(before: number | null | undefined, after: number | null | undefined): string {
  if (before == null || after == null || before === after) return "";
  const d = after - before;
  return d > 0 ? `+${d.toLocaleString("en-US")}` : `−${Math.abs(d).toLocaleString("en-US")}`;
}
