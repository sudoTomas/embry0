import { describe, it, expect } from "vitest";
import {
  MAX_DIFF_CELLS,
  diffLines,
  documentProblems,
  formatSettingValue,
  parseUnifiedDiff,
  unknownParts,
  type DiffLine,
} from "../settingsDocument";

function refused(status: number, detail: unknown) {
  return { response: { status, data: { detail } } };
}

const text = (lines: DiffLine[]) => lines.map((l) => `${l.kind}:${l.text}`);

describe("documentProblems", () => {
  it("names the key of each problem, in the order the server listed them", () => {
    const problems = documentProblems(
      refused(422, [
        { loc: ["sample", "per_tick"], type: "less_than_equal", msg: "Input should be less than or equal to 20" },
        { loc: ["sample", "enabled"], type: "bool_type", msg: "Input should be a valid boolean" },
        { loc: ["smaple"], type: "unknown_section", msg: "'smaple' is not a settings section. Did you mean 'sample'?" },
      ]),
    );
    expect(problems).toEqual([
      { key: "sample.per_tick", msg: "Input should be less than or equal to 20" },
      { key: "sample.enabled", msg: "Input should be a valid boolean" },
      { key: "smaple", msg: "'smaple' is not a settings section. Did you mean 'sample'?" },
    ]);
  });

  it("gives a YAML syntax error, whose loc is empty, no key", () => {
    expect(documentProblems(refused(422, [{ loc: [], type: "yaml_syntax", msg: "Line 2, column 3: bad indent" }]))).toEqual([
      { key: null, msg: "Line 2, column 3: bad indent" },
    ]);
  });

  it("drops pydantic's 'Value error, ' prefix and never reads the rejected input", () => {
    const problems = documentProblems(
      refused(422, [{ loc: ["sample", "hours"], msg: "Value error, hours must be HH-HH", input: "s3cret-value" }]),
    );
    expect(problems).toEqual([{ key: "sample.hours", msg: "hours must be HH-HH" }]);
    expect(JSON.stringify(problems)).not.toContain("s3cret-value");
  });

  it("reads every kind of refusal the same way: by its loc, whatever its type", () => {
    const problems = documentProblems(
      refused(422, [
        { loc: ["sample", "per_tick"], type: "duplicate_key", msg: "'per_tick' is written twice, on lines 3 and 5; YAML would keep only the second. Keep one." },
        { loc: ["sample"], type: "yaml_alias", msg: "Aliases (*name) are not supported in a settings document." },
        { loc: [], type: "yaml_merge", msg: "Line 4: merge keys (<<) are not supported." },
        { loc: [], type: "too_deep", msg: "The document is nested too deeply." },
        { loc: [], type: "nul_character", msg: "The document cannot hold the NUL character." },
        { loc: ["sample", "hours", 0], type: "check_failed", msg: "The check of this value failed (ValueError)" },
      ]),
    );
    expect(problems).toEqual([
      { key: "sample.per_tick", msg: "'per_tick' is written twice, on lines 3 and 5; YAML would keep only the second. Keep one." },
      { key: "sample", msg: "Aliases (*name) are not supported in a settings document." },
      { key: null, msg: "Line 4: merge keys (<<) are not supported." },
      { key: null, msg: "The document is nested too deeply." },
      { key: null, msg: "The document cannot hold the NUL character." },
      { key: "sample.hours.0", msg: "The check of this value failed (ValueError)" },
    ]);
  });

  it("reads a string detail as one problem, and is null for anything but a 422", () => {
    expect(documentProblems(refused(422, "If-Match must be the generation"))).toEqual([
      { key: null, msg: "If-Match must be the generation" },
    ]);
    expect(documentProblems(refused(409, "The settings changed"))).toBeNull();
    expect(documentProblems(new Error("boom"))).toBeNull();
    expect(documentProblems(null)).toBeNull();
  });
});

describe("formatSettingValue", () => {
  it("quotes strings and writes everything else as JSON", () => {
    expect(formatSettingValue("09-18")).toBe('"09-18"');
    expect(formatSettingValue(24)).toBe("24");
    expect(formatSettingValue(false)).toBe("false");
    expect(formatSettingValue(null)).toBe("null");
    expect(formatSettingValue(["mon", "tue"])).toBe('["mon","tue"]');
    expect(formatSettingValue(undefined)).toBe("—");
  });
});

describe("parseUnifiedDiff", () => {
  it("drops the file headers and reads each line's kind", () => {
    const diff = "--- stored\n+++ proposed\n@@ -1,2 +1,2 @@\n version: 1\n-a: 1\n+a: 2\n";
    expect(text(parseUnifiedDiff(diff))).toEqual(["gap:@@ -1,2 +1,2 @@", "same:version: 1", "remove:a: 1", "add:a: 2"]);
  });

  it("reads git's bare no-newline marker as a note, not as a change or as context", () => {
    const diff = "--- stored\n+++ proposed\n@@ -1,2 +1,2 @@\n a: 1\n-b: 2\n\\ No newline at end of file\n+b: 2\n";
    expect(text(parseUnifiedDiff(diff))).toEqual([
      "gap:@@ -1,2 +1,2 @@",
      "same:a: 1",
      "remove:b: 2",
      "note:\\ No newline at end of file",
      "add:b: 2",
    ]);
  });

  it("is empty for an empty diff", () => {
    expect(parseUnifiedDiff("")).toEqual([]);
  });
});

// What the server's text_diff (`git diff --no-index` exactly) writes for each
// case, copied verbatim from embry0.settings.document.text_diff.
const SERVER_DIFFS: [string, string, string, string][] = [
  ["both_lack", "a: 1\nb: 2", "a: 1\nb: 3", "--- stored\n+++ proposed\n@@ -1,2 +1,2 @@\n a: 1\n-b: 2\n\\ No newline at end of file\n+b: 3\n\\ No newline at end of file\n"],
  ["add_newline", "a: 1\nb: 2", "a: 1\nb: 2\n", "--- stored\n+++ proposed\n@@ -1,2 +1,2 @@\n a: 1\n-b: 2\n\\ No newline at end of file\n+b: 2\n"],
  ["drop_newline", "a: 1\nb: 2\n", "a: 1\nb: 2", "--- stored\n+++ proposed\n@@ -1,2 +1,2 @@\n a: 1\n-b: 2\n+b: 2\n\\ No newline at end of file\n"],
  ["append_unterminated", "a: 1\n", "a: 1\nb: 2", "--- stored\n+++ proposed\n@@ -1 +1,2 @@\n a: 1\n+b: 2\n\\ No newline at end of file\n"],
  ["from_empty", "", "x: 1", "--- stored\n+++ proposed\n@@ -0,0 +1 @@\n+x: 1\n\\ No newline at end of file\n"],
  ["context_only", "a: 0\nb: 2", "a: 1\nb: 2", "--- stored\n+++ proposed\n@@ -1,2 +1,2 @@\n-a: 0\n+a: 1\n b: 2\n\\ No newline at end of file\n"],
];

describe("the server's diff and this one", () => {
  it.each(SERVER_DIFFS)("agree line by line: %s", (_name, before, after, serverDiff) => {
    const server = parseUnifiedDiff(serverDiff).filter((line) => line.kind !== "gap");
    expect(text(diffLines(before, after))).toEqual(text(server));
    // git's marker is never read as a change or as context.
    expect(server.filter((line) => line.text.includes("No newline")).every((line) => line.kind === "note")).toBe(true);
  });
});

describe("diffLines", () => {
  it("marks removed and added lines and keeps the common ones", () => {
    expect(text(diffLines("a\nb\nc\n", "a\nB\nc\nd\n"))).toEqual(["same:a", "remove:b", "add:B", "same:c", "add:d"]);
  });

  it("reads a document written from nothing as every line added", () => {
    expect(text(diffLines("", "version: 1\nsample:\n"))).toEqual(["add:version: 1", "add:sample:"]);
  });

  it("folds a long unchanged run, keeping two lines of context next to the change", () => {
    const before = ["one", "two", "three", "four", "five", "six", "seven", "eight", ""].join("\n");
    const after = before.replace("eight", "EIGHT");
    expect(text(diffLines(before, after))).toEqual([
      "gap:5 unchanged lines",
      "same:six",
      "same:seven",
      "remove:eight",
      "add:EIGHT",
    ]);
  });

  it("marks a last line with no newline git's way, so a newline added or removed shows", () => {
    const note = "note:\\ No newline at end of file";
    // The newline added: the old last line goes, the new one comes, and the note says which lacked it.
    expect(text(diffLines("a: 1\nb: 2", "a: 1\nb: 2\n"))).toEqual(["same:a: 1", "remove:b: 2", note, "add:b: 2"]);
    expect(text(diffLines("a: 1\n", "a: 1\nb: 2"))).toEqual(["same:a: 1", "add:b: 2", note]);
    // Both lack it and nothing else changed there: the last line is context, with the note.
    expect(text(diffLines("a: 1\nb: 2", "a: 0\nb: 2"))).toEqual(["remove:a: 1", "add:a: 0", "same:b: 2", note]);
  });

  it("folds an identical text to one gap", () => {
    expect(text(diffLines("a\nb\n", "a\nb\n"))).toEqual(["gap:2 unchanged lines"]);
  });

  it("past the size limit, removes every old line and adds every new one rather than search", () => {
    const side = Math.ceil(Math.sqrt(MAX_DIFF_CELLS)) + 1;
    const before = Array.from({ length: side }, (_, i) => `old ${i}\n`).join("");
    const after = Array.from({ length: side }, (_, i) => `new ${i}\n`).join("");
    const lines = diffLines(before, after);
    expect(lines).toHaveLength(side * 2);
    expect(lines[0]).toEqual({ kind: "remove", text: "old 0" });
    expect(lines[side]).toEqual({ kind: "add", text: "new 0" });
  });
});

describe("unknownParts", () => {
  it("names the sections and keys a newer release wrote, and is null when there are none", () => {
    expect(unknownParts(["later"], [])).toBe("section later");
    expect(unknownParts([], ["sample.extra"])).toBe("key sample.extra");
    expect(unknownParts(["later", "other"], ["sample.extra", "sample.more"])).toBe(
      "sections later, other and keys sample.extra, sample.more",
    );
    expect(unknownParts([], [])).toBeNull();
  });
});
