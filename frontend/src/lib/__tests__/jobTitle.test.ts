import { describe, it, expect } from "vitest";
import watcherSource from "../../../../embry0/services/watcher.py?raw";
import { jobTitle, shortJobId } from "../jobTitle";

// The watcher's task template, read from the backend so a change to its
// wording fails here instead of silently turning every tick back into the
// same truncated prompt on the Jobs list.
function watcherFirstLine(values: Record<string, string>): string {
  const match = /_ANALYSIS_TASK_TEMPLATE = """([\s\S]*?)"""/.exec(watcherSource);
  if (!match) throw new Error("_ANALYSIS_TASK_TEMPLATE not found in embry0/services/watcher.py");
  const text = match[1].replace(/\\\n/g, "").replace(/\{(\w+)\}/g, (_, k: string) => values[k] ?? `{${k}}`);
  return text.split("\n", 1)[0];
}

describe("jobTitle", () => {
  const line = watcherFirstLine({
    stack: "acme/backend", window_start: "2026-10-19 00:32", window_end: "2026-10-19 01:34", line_count: "22",
  });

  it("names a watcher tick from the backend's own template", () => {
    expect(jobTitle(line)).toBe("Watcher tick, backend, 22 lines");
  });

  it("still names the tick when a brief list cut the line before its count", () => {
    expect(jobTitle(line.slice(0, 80))).toBe("Watcher tick, backend");
  });

  it("is the first line of any other task, without a heading mark", () => {
    expect(jobTitle("## Design audit of acme/app\n\nAudit this repository")).toBe("Design audit of acme/app");
  });
});

describe("shortJobId", () => {
  it("keeps 8 hex characters after the job- prefix", () => {
    expect(shortJobId("job-fd155299c8a4")).toBe("fd155299");
  });
});
