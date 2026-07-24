import { describe, expect, it } from "vitest";
import {
  OPERATIONS,
  OPERATION_ELEMENT,
  OPERATION_NUMERAL,
  agentTypeToOperation,
  jobToOperation,
  shouldFireCompletionFlare,
  type Operation,
} from "../operations";

describe("operations", () => {
  it("exposes exactly the seven canonical operations in order", () => {
    expect(OPERATIONS).toEqual([
      "calcinate",
      "dissolve",
      "separate",
      "conjoin",
      "ferment",
      "distill",
      "coagulate",
    ]);
  });

  it("maps every operation to its element", () => {
    for (const op of OPERATIONS) {
      expect(OPERATION_ELEMENT[op]).toBeTruthy();
    }
  });

  it("maps every operation to its Roman numeral position I..VII", () => {
    expect(OPERATION_NUMERAL.calcinate).toBe("I");
    expect(OPERATION_NUMERAL.dissolve).toBe("II");
    expect(OPERATION_NUMERAL.separate).toBe("III");
    expect(OPERATION_NUMERAL.conjoin).toBe("IV");
    expect(OPERATION_NUMERAL.ferment).toBe("V");
    expect(OPERATION_NUMERAL.distill).toBe("VI");
    expect(OPERATION_NUMERAL.coagulate).toBe("VII");
  });

  it("Operation type accepts only the seven canonical names", () => {
    // Compile-time check: this reassignment must be assignable.
    const op: Operation = "calcinate";
    expect(OPERATIONS).toContain(op);
  });
});

describe("agentTypeToOperation", () => {
  it.each([
    ["triage", "calcinate"],
    ["developer", "ferment"],
    ["code-gen", "ferment"],
    ["docs-writer", "ferment"],
    ["explorer", "separate"],
    ["frontend-explorer", "separate"],
    ["reviewer", "distill"],
    ["security-reviewer", "distill"],
    ["review", "distill"],
    ["validator", "conjoin"],
    ["lint-checker", "conjoin"],
    ["type-checker", "conjoin"],
    ["test-runner", "conjoin"],
    ["visual-validator", "conjoin"],
    ["qa", "conjoin"],
    ["output", "coagulate"],
    ["publish", "coagulate"],
    ["research", "separate"],
    ["analysis", "distill"],
    ["ops", "ferment"],
  ])("maps %s → %s", (agentType, expected) => {
    expect(agentTypeToOperation(agentType)).toBe(expected);
  });

  it("returns undefined for unknown / custom agent types", () => {
    expect(agentTypeToOperation("custom-agent")).toBeUndefined();
    expect(agentTypeToOperation("")).toBeUndefined();
    expect(agentTypeToOperation(undefined)).toBeUndefined();
    expect(agentTypeToOperation(null)).toBeUndefined();
  });

  it("normalizes case (returns same operation regardless of input casing)", () => {
    expect(agentTypeToOperation("DEVELOPER")).toBe("ferment");
    expect(agentTypeToOperation("Triage")).toBe("calcinate");
  });
});

describe("jobToOperation", () => {
  it("returns ferment for an empty pipeline (no agents observed yet)", () => {
    expect(jobToOperation([])).toBe("ferment");
  });

  it("returns the active agent's operation when one is running", () => {
    expect(
      jobToOperation([
        { agent: "triage", status: "completed" },
        { agent: "developer", status: "running" },
      ]),
    ).toBe("ferment");
  });

  it("prefers the active agent over completed history", () => {
    expect(
      jobToOperation([
        { agent: "triage", status: "completed" },
        { agent: "developer", status: "completed" },
        { agent: "reviewer", status: "running" },
      ]),
    ).toBe("distill");
  });

  it("falls back to the last completed agent when none are running", () => {
    expect(
      jobToOperation([
        { agent: "triage", status: "completed" },
        { agent: "developer", status: "completed" },
        { agent: "qa", status: "completed" },
      ]),
    ).toBe("conjoin");
  });

  it("counts a failed agent as terminal history (not as in-progress)", () => {
    expect(
      jobToOperation([
        { agent: "triage", status: "completed" },
        { agent: "developer", status: "failed" },
      ]),
    ).toBe("ferment");
  });

  it("falls back to ferment when the active agent type is unmapped", () => {
    expect(
      jobToOperation([{ agent: "custom-agent", status: "running" }]),
    ).toBe("ferment");
  });
});

describe("shouldFireCompletionFlare", () => {
  it("fires when running flips to completed", () => {
    expect(shouldFireCompletionFlare("running", "completed")).toBe(true);
  });

  it("fires when running flips to pr_merged", () => {
    expect(shouldFireCompletionFlare("running", "pr_merged")).toBe(true);
  });

  it("does NOT fire on first mount of an already-complete job", () => {
    expect(shouldFireCompletionFlare(null, "completed")).toBe(false);
    expect(shouldFireCompletionFlare(undefined, "pr_merged")).toBe(false);
  });

  it("does NOT fire when status hasn't actually changed", () => {
    expect(shouldFireCompletionFlare("completed", "completed")).toBe(false);
    expect(shouldFireCompletionFlare("pr_merged", "pr_merged")).toBe(false);
  });

  it("does NOT fire on failure terminals (rule #5: operator-critical)", () => {
    expect(shouldFireCompletionFlare("running", "failed")).toBe(false);
    expect(shouldFireCompletionFlare("running", "cancelled")).toBe(false);
    expect(shouldFireCompletionFlare("running", "expired")).toBe(false);
    expect(shouldFireCompletionFlare("running", "pr_closed")).toBe(false);
  });

  it("does NOT fire on intermediate transitions", () => {
    expect(shouldFireCompletionFlare("running", "awaiting_input")).toBe(false);
    expect(shouldFireCompletionFlare("pending", "running")).toBe(false);
    expect(shouldFireCompletionFlare("awaiting_input", "running")).toBe(false);
  });

  it("fires when awaiting_input flips to completed (resume → success)", () => {
    expect(shouldFireCompletionFlare("awaiting_input", "completed")).toBe(true);
  });
});
