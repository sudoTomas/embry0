// Pure helpers for the Policy tab: the problems of a refused write and the
// words for a value and a source. The line diff of two texts is shared with
// the Game content tab (@/lib/textDiff).

import type { SettingSource, SettingsRevisionSource } from "@/lib/types/settings";

/**
 * One problem of a refused document: the key it belongs to (null for the
 * whole text), why, and the server's `type` for it when it sent one, such as
 * `not_overridable` (a key the layer may not set) or
 * `conflicts_with_inherited`.
 */
export interface DocumentProblem {
  key: string | null;
  msg: string;
  type?: string;
}

/**
 * The problems of a 422 from a settings write, one per bad key, in the
 * order the server listed them; null for any other failure. The detail is
 * FastAPI's list (`{loc, type, msg}`); `loc` is the path from the
 * document's root, empty for a YAML syntax error. `input` is never read.
 */
export function documentProblems(error: unknown): DocumentProblem[] | null {
  const response = (error as { response?: { status?: unknown; data?: { detail?: unknown } } } | null)?.response;
  if (response?.status !== 422) return null;
  const detail = response.data?.detail;
  if (typeof detail === "string") return [{ key: null, msg: detail }];
  if (!Array.isArray(detail)) return [{ key: null, msg: "The document was refused." }];
  return detail.flatMap((item): DocumentProblem[] => {
    if (!item || typeof item !== "object") return [];
    const { loc, msg, type } = item as { loc?: unknown; msg?: unknown; type?: unknown };
    if (typeof msg !== "string" || !msg.trim()) return [];
    const path = Array.isArray(loc) ? loc.map(String).join(".") : "";
    // pydantic prefixes a validator's ValueError with "Value error, ".
    const problem: DocumentProblem = { key: path || null, msg: msg.trim().replace(/^Value error, /, "") };
    if (typeof type === "string" && type) problem.type = type;
    return [problem];
  });
}

/** A setting's value as YAML-minded text: strings quoted, everything else as JSON. */
export function formatSettingValue(value: unknown): string {
  if (value === undefined) return "—";
  return JSON.stringify(value) ?? String(value);
}

/**
 * The stored sections and keys a newer release wrote and this one does not
 * read, in words (`section later and key sample.extra`); null when none.
 */
export function unknownParts(sections: string[], keys: string[]): string | null {
  const parts: string[] = [];
  if (sections.length > 0) parts.push(`${sections.length === 1 ? "section" : "sections"} ${sections.join(", ")}`);
  if (keys.length > 0) parts.push(`${keys.length === 1 ? "key" : "keys"} ${keys.join(", ")}`);
  return parts.length > 0 ? parts.join(" and ") : null;
}

export const SOURCE_LABELS: Record<SettingSource, string> = {
  default: "Default",
  document: "Set in the document",
  bootstrap: "Imported from the environment",
  organization: "Set by the organization",
  repository: "Set by the repository",
};

export const REVISION_SOURCE_LABELS: Record<SettingsRevisionSource, string> = {
  person: "Dashboard or API",
  file: "Settings file",
  bootstrap: "Imported from the environment",
  restore: "Undo",
  upgrade: "Upgrade",
};
