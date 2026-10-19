import { describe, expect, it } from "vitest";
import { providerLabel, statusLabel, statusTone } from "../status";

describe("statusLabel — readable words for every status, in one place", () => {
  it.each([
    ["pr_merged", "PR merged"],
    ["PR_MERGED", "PR merged"],
    ["completed", "Completed"],
    ["paused", "Paused"],
    ["awaiting_input", "Awaiting input"],
    ["skipped_quiet", "Quiet"],
    ["no_issue", "Nothing found"],
    ["qa_failure", "QA failure"],
    ["ok", "OK"],
  ])("%s reads as %s", (raw, label) => {
    expect(statusLabel(raw)).toBe(label);
  });

  it("turns a status it does not know yet into sentence case, never the raw enum", () => {
    expect(statusLabel("SOMETHING_NEWER")).toBe("Something newer");
    expect(statusLabel("waiting-on-ci")).toBe("Waiting on ci");
  });

  it("says Unknown for a missing status", () => {
    expect(statusLabel(null)).toBe("Unknown");
    expect(statusLabel(undefined)).toBe("Unknown");
  });
});

describe("statusTone", () => {
  it("colours outcomes by what they mean", () => {
    expect(statusTone("completed")).toBe("success");
    expect(statusTone("failed")).toBe("error");
    expect(statusTone("paused")).toBe("warning");
    expect(statusTone("running")).toBe("info");
    expect(statusTone("pr_merged")).toBe("purple");
  });

  it("falls back to neutral for an unknown status", () => {
    expect(statusTone("something_newer")).toBe("neutral");
    expect(statusTone(undefined)).toBe("neutral");
  });
});

describe("providerLabel — provider names as their makers spell them", () => {
  it.each([
    ["anthropic", "Anthropic"],
    ["xai", "xAI"],
    ["openai", "OpenAI"],
    ["github", "GitHub"],
    ["GitLab", "GitLab"],
    ["linear", "Linear"],
    ["telegram", "Telegram"],
  ])("%s -> %s", (raw, words) => {
    expect(providerLabel(raw)).toBe(words);
  });
});

describe("statusTone — gold is the accent, never a status", () => {
  it("draws a proposed draft in the warn colour, like other work waiting for a person", () => {
    expect(statusTone("proposed")).toBe("warning");
    expect(statusTone("awaiting_input")).toBe("warning");
  });
});
