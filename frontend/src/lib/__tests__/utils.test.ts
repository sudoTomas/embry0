import { describe, it, expect } from "vitest";
import { cn, extractRole, formatDateTime, formatDay, formatTime, highlightJson } from "../utils";

describe("cn", () => {
  it("merges class names", () => {
    expect(cn("foo", "bar")).toBe("foo bar");
  });

  it("handles conditional classes", () => {
    const isHidden = false;
    expect(cn("base", isHidden && "hidden", "extra")).toBe("base extra");
  });
});

// A subagent is named after its Task call's subagent_type, else its
// description, else the tool's own name (the executor's tool_use handling).
describe("extractRole", () => {
  it("reads the built-in Explore subagent as the explorer", () => {
    expect(extractRole("Explore")).toBe("explorer");
    expect(extractRole("explorer")).toBe("explorer");
    expect(extractRole("frontend-explorer")).toBe("explorer");
    expect(extractRole("Explore the repository layout")).toBe("explorer");
  });

  it("keeps the other roles' names as they were", () => {
    expect(extractRole("developer_agent")).toBe("developer");
    expect(extractRole("validator_v2")).toBe("validator");
    expect(extractRole("triage")).toBe("triage");
  });

  it("names no role for a subagent that has none", () => {
    for (const name of ["general-purpose", "Plan", "Task", "Agent"]) {
      expect(extractRole(name), name).toBeUndefined();
    }
  });
});

describe("highlightJson", () => {
  it("reassembles to the input text unchanged", () => {
    const json = JSON.stringify({ a: 1, b: "x", c: [true, null] }, null, 2);
    const text = highlightJson(json).map((s) => s.text).join("");
    expect(text).toBe(json);
  });
});

describe("formatDateTime", () => {
  const now = new Date(2026, 9, 18, 12, 0);

  it("drops the year inside the current one and keeps a 24-hour clock", () => {
    expect(formatDateTime(new Date(2026, 8, 8, 13, 34, 57), now)).toBe("Sep 8, 13:34");
  });

  it("names the year when it differs", () => {
    expect(formatDateTime(new Date(2025, 11, 30, 9, 5), now)).toBe("Dec 30, 2025 09:05");
  });

  it("reads the API's space-separated timestamps", () => {
    const local = formatDateTime(new Date("2026-09-08T13:34:46.646096+00:00"), now);
    expect(formatDateTime("2026-09-08 13:34:46.646096+00:00", now)).toBe(local);
  });

  it("shows a value that does not parse as it came", () => {
    expect(formatDateTime("not a date", now)).toBe("not a date");
  });
});

describe("formatDay", () => {
  const now = new Date(2026, 9, 18, 12, 0);

  it("is the date part of the table format: no year inside the current one", () => {
    expect(formatDay(new Date(2026, 8, 8, 13, 34), now)).toBe("Sep 8");
  });

  it("names the year when it differs", () => {
    expect(formatDay(new Date(2025, 11, 30, 9, 5), now)).toBe("Dec 30, 2025");
  });

  it("shows a value that does not parse as it came", () => {
    expect(formatDay("not a date", now)).toBe("not a date");
  });
});

describe("formatTime", () => {
  it("is a 24-hour clock time", () => {
    expect(formatTime(new Date(2026, 9, 18, 23, 49))).toBe("23:49");
  });
});
