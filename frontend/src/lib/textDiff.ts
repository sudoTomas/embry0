// The line diff of a document's two texts, shared by every tab that shows one
// (the Policy tab's settings document, the Game content tab's pack): a
// unified diff as the API writes it (git's format, `\\ No newline at end of
// file` included), and the same diff computed here when only the texts are
// known.

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
