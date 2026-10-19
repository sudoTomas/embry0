import { describe, it, expect } from "vitest";
import { cn, formatDateTime, formatDay, formatTime, highlightJson } from "../utils";

describe("cn", () => {
  it("merges class names", () => {
    expect(cn("foo", "bar")).toBe("foo bar");
  });

  it("handles conditional classes", () => {
    const isHidden = false;
    expect(cn("base", isHidden && "hidden", "extra")).toBe("base extra");
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
