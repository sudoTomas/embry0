import { describe, it, expect } from "vitest";
import { isTerminalStatus } from "../jobStatus";

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
