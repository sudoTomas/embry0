import { describe, it, expect } from "vitest";
import { Dot, KeyRound, Plus } from "lucide-react";
import { ACTION_ICONS, ACTION_LABELS, actionIcon, formatDetails } from "../auditLabels";

describe("auditLabels", () => {
  it("labels the issue events lifted from IssueActivityFeed", () => {
    expect(ACTION_LABELS["issue.created"]).toBe("Created");
    expect(ACTION_ICONS["issue.created"]).toBe(Plus);
  });

  it("draws a lucide icon for every labelled action and a dot for an unknown one, never an emoji", () => {
    for (const action of Object.keys(ACTION_LABELS)) {
      expect(typeof actionIcon(action), action).not.toBe("string");
    }
    expect(actionIcon("user.login")).toBe(KeyRound);
    expect(actionIcon("something.new")).toBe(Dot);
  });

  it("labels the Phase A/B/C identity events", () => {
    expect(ACTION_LABELS["user.login"]).toBe("Signed in");
    expect(ACTION_LABELS["user.enabled"]).toBe("User enabled");
    expect(ACTION_LABELS["apikey.revoked"]).toBe("API key revoked");
    expect(ACTION_LABELS["context_config_deleted"]).toBe("Context deleted");
  });

  it("formats issue.status_changed transitions", () => {
    expect(formatDetails("issue.status_changed", { old_status: "open", new_status: "done" })).toBe(
      "open → done",
    );
  });

  it("formats user lifecycle details", () => {
    expect(formatDetails("user.login", { tier: "admin" })).toBe("tier admin");
    expect(formatDetails("user.disabled", { email: "x@y.com" })).toBe("x@y.com");
  });

  it("formats API key details with scope and service marker", () => {
    expect(formatDetails("apikey.created", { name: "hermes", scope: "operate", service: true })).toBe(
      "hermes (operate) · service",
    );
    expect(formatDetails("apikey.revoked", { name: "old" })).toBe("old");
  });

  it("formats context scope and provider test status", () => {
    expect(formatDetails("context_config_updated", { scope: "global" })).toBe("global");
    expect(formatDetails("context_config_updated", { scope: "repo", repo: "o/r" })).toBe("o/r");
    expect(formatDetails("provider_config_tested", { status: "ok" })).toBe("ok");
  });

  it("shows changed-field names for config updates without leaking values", () => {
    const out = formatDetails("provider_config_updated", { model_heavy: "claude-opus-5", api_key: "sk-x" });
    expect(out).toBe("model_heavy, api_key");
    expect(out).not.toContain("sk-x");
  });

  it("falls back to empty detail for unknown actions", () => {
    expect(formatDetails("something.new", { a: 1 })).toBe("");
  });
});
