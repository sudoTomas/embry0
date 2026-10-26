import type { DoneWhenEdit } from "@/lib/types/jobs";

/** One editor line as a condition and how it is shown: "condition — how", "condition -- how" or "condition | how". */
export function parseDoneWhenLines(items: string, scope: string): DoneWhenEdit {
  const lines = (text: string) =>
    text
      .split("\n")
      .map((line) => line.replace(/^\s*(?:[-*]\s+)?/, "").trim())
      .filter(Boolean);
  return {
    items: lines(items).map((line) => {
      const match = line.match(/\s+(?:—|–|--|\|)\s+/);
      if (!match || match.index === undefined) return { done: line, shown_by: "" };
      return { done: line.slice(0, match.index).trim(), shown_by: line.slice(match.index + match[0].length).trim() };
    }),
    out_of_scope: lines(scope),
  };
}
