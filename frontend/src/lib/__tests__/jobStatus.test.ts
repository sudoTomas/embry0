import { describe, it, expect } from "vitest";
import { isSuccessfulTerminal, isTerminalStatus } from "../jobStatus";

describe("isTerminalStatus", () => {
  it("treats partial as terminal (#62)", () => {
    expect(isTerminalStatus("partial"), "partial is a terminal job status").toBe(true);
  });

  it("is false for live statuses and a missing status", () => {
    expect(isTerminalStatus("running")).toBe(false);
    expect(isTerminalStatus("awaiting_input")).toBe(false);
    expect(isTerminalStatus(undefined)).toBe(false);
  });
});

describe("isSuccessfulTerminal", () => {
  it("is true for the ok terminal statuses only", () => {
    for (const s of ["completed", "pr_merged", "skipped"]) expect(isSuccessfulTerminal(s), s).toBe(true);
    for (const s of ["failed", "partial", "cancelled", "running", undefined]) expect(isSuccessfulTerminal(s), String(s)).toBe(false);
  });
});
