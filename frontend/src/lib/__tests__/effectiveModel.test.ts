import { describe, it, expect } from "vitest";
import { runModelOf, runModelReason, runOverrideNote } from "../effectiveModel";
import type { AgentDefinition } from "../types/agents";

const STORED: AgentDefinition = {
  type: "analysis",
  description: "",
  model: "claude-sonnet-5",
  tools: [],
  skills: [],
  system_prompt: "",
  is_builtin: true,
  provider: "anthropic",
  provider_ready: true,
};

const ON_DEFAULT: AgentDefinition = {
  ...STORED,
  effective_model: "grok-4.7",
  effective_model_source: "default_model",
  effective_provider: "xai",
  effective_provider_ready: false,
};

describe("runModelOf", () => {
  it("reports the effective model, provider and readiness, not the stored ones", () => {
    expect(runModelOf(ON_DEFAULT)).toEqual({
      model: "grok-4.7",
      provider: "xai",
      ready: false,
      reason: "Deployment default model",
    });
  });

  it("falls back to the stored fields on a backend without effective ones", () => {
    expect(runModelOf(STORED)).toEqual({ model: "claude-sonnet-5", provider: "anthropic", ready: true, reason: null });
  });

  it("gives no reason when the agent runs its own model", () => {
    const own = { ...STORED, effective_model: "claude-sonnet-5", effective_model_source: "agent", effective_provider: "anthropic", effective_provider_ready: true };
    expect(runModelOf(own).reason).toBeNull();
  });
});

describe("runModelReason", () => {
  it.each([
    ["agent", null],
    [undefined, null],
    ["default_model", "Deployment default model"],
    ["disabled_fallback", "Stand-in for a disabled model"],
    ["builtin_default", "Built-in default"],
    ["per_job_heavy_tier", "Heavy tier, named by every job"],
    ["per_job_light_tier", "Light tier, named by every job"],
    ["some_new_source", "some new source"],
  ])("%s -> %s", (source, reason) => {
    expect(runModelReason(source)).toBe(reason);
  });
});

describe("runOverrideNote", () => {
  it("says the default model replaces whatever model is chosen", () => {
    expect(runOverrideNote(ON_DEFAULT, "claude-sonnet-5")).toContain("grok-4.7, the deployment default model");
    expect(runOverrideNote(ON_DEFAULT, "claude-haiku-4-5")).toContain("grok-4.7, the deployment default model");
  });

  it("says a per-job tier replaces whatever model is chosen", () => {
    const reviewer = { ...STORED, type: "pr-reviewer", model: "claude-opus-5", effective_model: "claude-opus-5", effective_model_source: "per_job_heavy_tier" };
    expect(runOverrideNote(reviewer, "grok-4.7")).toBe(
      "Every job runs this agent on the heavy tier, now claude-opus-5, not on the model chosen here.",
    );
  });

  it("names a disabled-model stand-in only while the saved model is chosen", () => {
    const disabled = { ...STORED, effective_model: "claude-haiku-4-5", effective_model_source: "disabled_fallback" };
    expect(runOverrideNote(disabled, "claude-sonnet-5")).toBe("This model is disabled, so jobs run claude-haiku-4-5 in its place.");
    expect(runOverrideNote(disabled, "claude-opus-5")).toBeNull();
  });

  it("says nothing when the agent runs its own model, or the backend sends no effective model", () => {
    expect(runOverrideNote({ ...STORED, effective_model: "claude-sonnet-5", effective_model_source: "agent" }, "claude-sonnet-5")).toBeNull();
    expect(runOverrideNote(STORED, "claude-sonnet-5")).toBeNull();
    expect(runOverrideNote(undefined, "claude-sonnet-5")).toBeNull();
  });
});
