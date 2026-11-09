import { describe, it, expect } from "vitest";
import { Dot, KeyRound, Plus, Settings } from "lucide-react";
import { ACTION_ICONS, auditVocabulary, formatDetails } from "../auditLabels";

/** A registry as GET /audit/actions serves one: current actions, then the retired names with their replacements. */
const REGISTRY = [
  { action: "issue.created", label: "Created", replaced_by: null },
  { action: "user.login", label: "Signed in", replaced_by: null },
  { action: "settings.updated", label: "Settings document changed", replaced_by: null },
  { action: "credential.bound", label: "Credential bound", replaced_by: null },
  { action: "job.review.dismissed", label: "Job dismissed", replaced_by: null },
  { action: "budget_config_updated", label: "Settings document changed", replaced_by: "settings.updated" },
  { action: "quest.dismissed", label: "Job dismissed", replaced_by: "job.review.dismissed" },
];

describe("auditLabels", () => {
  it("reads an action in the registry's words, and one it does not list in sentence case, never raw", () => {
    const vocabulary = auditVocabulary(REGISTRY);
    expect(vocabulary.label("issue.created")).toBe("Created");
    expect(vocabulary.label("credential.bound")).toBe("Credential bound");
    expect(vocabulary.label("organization.need_reopen")).toBe("Organization need reopen");
    expect(auditVocabulary().label("user.login"), "before the registry is read").toBe("User login");
  });

  it("offers the current actions in the registry's order and finds a retired name's rows by its replacement", () => {
    expect(auditVocabulary(REGISTRY).filterOptions).toEqual([
      "issue.created",
      "user.login",
      "settings.updated",
      "credential.bound",
      "job.review.dismissed",
    ]);
    expect(auditVocabulary().filterOptions).toEqual([]);
  });

  it("draws a lucide icon for every action that has one and a dot for any other, never an emoji", () => {
    const vocabulary = auditVocabulary(REGISTRY);
    for (const action of Object.keys(ACTION_ICONS)) {
      expect(typeof vocabulary.icon(action), action).not.toBe("string");
    }
    expect(vocabulary.icon("issue.created")).toBe(Plus);
    expect(vocabulary.icon("user.login")).toBe(KeyRound);
    expect(vocabulary.icon("something.new")).toBe(Dot);
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

  it("names the keys a settings change touched, never a value", () => {
    expect(
      formatDetails("settings.updated", { keys: ["sample.per_tick", "sample.enabled"], reason: "r", revision_id: 9 }),
    ).toBe("sample.per_tick, sample.enabled");
    expect(formatDetails("settings.updated", { keys: [], revision_id: 9 })).toBe("comments only");
    expect(formatDetails("settings.bootstrapped", { sections: { sample: ["enabled", "per_tick"] }, revision_id: 1 })).toBe(
      "sample.enabled, sample.per_tick",
    );
  });

  it("reads a retired name as the action that replaced it, in words and icon, and never offers it twice", () => {
    const vocabulary = auditVocabulary(REGISTRY);
    expect(vocabulary.label("quest.dismissed"), "an older row keeps reading after the rename").toBe("Job dismissed");
    expect(vocabulary.icon("budget_config_updated")).toBe(Settings);
    expect(vocabulary.filterOptions, "the Audit filter offers the new name").toContain("job.review.dismissed");
    expect(vocabulary.filterOptions, "the old name is found by the new name's filter").not.toContain("quest.dismissed");
  });

  it("falls back to empty detail for unknown actions", () => {
    expect(formatDetails("something.new", { a: 1 })).toBe("");
  });
});
