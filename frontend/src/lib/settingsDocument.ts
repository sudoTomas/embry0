// Pure helpers for the Policy tab: the problems of a refused write, the
// words for a value and a source, and the line diff of two texts.

import type { SettingSource, SettingsRevisionSource } from "@/lib/types/settings";

/** One problem of a refused document: the key it belongs to (null for the whole text) and why. */
export interface DocumentProblem {
  key: string | null;
  msg: string;
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
    const { loc, msg } = item as { loc?: unknown; msg?: unknown };
    if (typeof msg !== "string" || !msg.trim()) return [];
    const path = Array.isArray(loc) ? loc.map(String).join(".") : "";
    // pydantic prefixes a validator's ValueError with "Value error, ".
    return [{ key: path || null, msg: msg.trim().replace(/^Value error, /, "") }];
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
};

export const REVISION_SOURCE_LABELS: Record<SettingsRevisionSource, string> = {
  person: "Dashboard or API",
  file: "Settings file",
  bootstrap: "Imported from the environment",
  restore: "Undo",
  upgrade: "Upgrade",
};

/** `note` is git's "\\ No newline at end of file": about the line before it, not a line of the text. */
export type DiffLineKind = "same" | "add" | "remove" | "gap" | "note";

export interface DiffLine {
  kind: DiffLineKind;
  text: string;
}

/** git's note after a last line that has no newline; the server's diff (`git diff --no-index`) writes it the same way. */
export const NO_NEWLINE = "\\ No newline at end of file";

// Ends a last line that has no newline while the diff runs, so it differs
// from the same words with one, as git counts it. A settings document
// cannot hold NUL, so no real line ends with it.
const UNTERMINATED = "\u0000";

function rawLines(text: string): string[] {
  return text === "" ? [] : text.replace(/\n$/, "").split("\n");
}

/** A text's lines; a last line with no newline is marked as such. */
function lines(text: string): string[] {
  const out = rawLines(text);
  if (text !== "" && !text.endsWith("\n")) out[out.length - 1] += UNTERMINATED;
  return out;
}

/** Each marked last line, unmarked, with git's note after it. */
function withNotes(all: DiffLine[]): DiffLine[] {
  return all.flatMap((line) =>
    line.text.endsWith(UNTERMINATED)
      ? [{ kind: line.kind, text: line.text.slice(0, -UNTERMINATED.length) }, { kind: "note" as const, text: NO_NEWLINE }]
      : [line],
  );
}

/**
 * The lines of a unified diff as the API writes it (`--- stored`,
 * `+++ proposed`, then hunks). The file headers are dropped; a hunk header
 * becomes a gap.
 */
export function parseUnifiedDiff(diff: string): DiffLine[] {
  const out: DiffLine[] = [];
  for (const line of rawLines(diff)) {
    if (line.startsWith("--- ") || line.startsWith("+++ ")) continue;
    if (line.startsWith("@@")) out.push({ kind: "gap", text: line });
    else if (line.startsWith("+")) out.push({ kind: "add", text: line.slice(1) });
    else if (line.startsWith("-")) out.push({ kind: "remove", text: line.slice(1) });
    // git's "\\ No newline at end of file": a note on the line before it.
    else if (line.startsWith("\\")) out.push({ kind: "note", text: line });
    else out.push({ kind: "same", text: line.slice(1) });
  }
  return out;
}

/** Past this many cells the diff stops looking for common lines: every old line is removed, every new one added. */
export const MAX_DIFF_CELLS = 2_000_000;

/**
 * The line diff of two texts (longest common subsequence), unchanged runs
 * longer than twice `context` folded into a gap that says how many lines it
 * hides.
 */
export function diffLines(before: string, after: string, context = 2): DiffLine[] {
  const a = lines(before);
  const b = lines(after);
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start++;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) {
    endA--;
    endB--;
  }
  const midA = a.slice(start, endA);
  const midB = b.slice(start, endB);
  const middle: DiffLine[] = [];
  if (midA.length * midB.length > MAX_DIFF_CELLS) {
    middle.push(...midA.map((text) => ({ kind: "remove" as const, text })));
    middle.push(...midB.map((text) => ({ kind: "add" as const, text })));
  } else {
    const n = midA.length;
    const m = midB.length;
    const width = m + 1;
    // common[i * width + j]: the longest common run of midA[i..] and midB[j..].
    const common = new Uint32Array((n + 1) * width);
    for (let i = n - 1; i >= 0; i--) {
      for (let j = m - 1; j >= 0; j--) {
        common[i * width + j] =
          midA[i] === midB[j]
            ? common[(i + 1) * width + j + 1] + 1
            : Math.max(common[(i + 1) * width + j], common[i * width + j + 1]);
      }
    }
    let i = 0;
    let j = 0;
    while (i < n && j < m) {
      if (midA[i] === midB[j]) {
        middle.push({ kind: "same", text: midA[i] });
        i++;
        j++;
      } else if (common[(i + 1) * width + j] >= common[i * width + j + 1]) {
        middle.push({ kind: "remove", text: midA[i++] });
      } else {
        middle.push({ kind: "add", text: midB[j++] });
      }
    }
    while (i < n) middle.push({ kind: "remove", text: midA[i++] });
    while (j < m) middle.push({ kind: "add", text: midB[j++] });
  }
  const all: DiffLine[] = [
    ...a.slice(0, start).map((text) => ({ kind: "same" as const, text })),
    ...middle,
    ...a.slice(endA).map((text) => ({ kind: "same" as const, text })),
  ];
  return foldUnchanged(withNotes(all), context);
}

function foldUnchanged(all: DiffLine[], context: number): DiffLine[] {
  const out: DiffLine[] = [];
  let index = 0;
  while (index < all.length) {
    if (all[index].kind !== "same") {
      out.push(all[index++]);
      continue;
    }
    let end = index;
    while (end < all.length && all[end].kind === "same") end++;
    const run = all.slice(index, end);
    // Keep `context` lines next to each change; the ends of the text need none on their outer side.
    const keepBefore = index === 0 ? 0 : context;
    const keepAfter = end === all.length ? 0 : context;
    if (run.length > keepBefore + keepAfter + 1) {
      const hidden = run.length - keepBefore - keepAfter;
      out.push(...run.slice(0, keepBefore));
      out.push({ kind: "gap", text: `${hidden} unchanged ${hidden === 1 ? "line" : "lines"}` });
      out.push(...run.slice(run.length - keepAfter));
    } else {
      out.push(...run);
    }
    index = end;
  }
  return out;
}
