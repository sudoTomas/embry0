import { describe, it, expect } from "vitest";
import { providerForModel } from "../providers";

const ANTHROPIC = {
  name: "anthropic",
  available: true,
  model_prefixes: ["claude-"],
  models: [{ model: "claude-opus-5" }],
};
const XAI = { name: "xai", available: false, model_prefixes: ["grok-"], models: [{ model: "grok-4.5" }] };

describe("providerForModel", () => {
  it("routes by id prefix, custom ids included", () => {
    expect(providerForModel("claude-opus-5", [ANTHROPIC, XAI])).toBe(ANTHROPIC);
    expect(providerForModel("claude-some-future-model", [ANTHROPIC, XAI])).toBe(ANTHROPIC);
    expect(providerForModel("grok-9", [ANTHROPIC, XAI])).toBe(XAI);
  });

  it("returns null for an id no provider serves and for an empty id", () => {
    expect(providerForModel("gpt-4o", [ANTHROPIC, XAI])).toBeNull();
    expect(providerForModel("", [ANTHROPIC, XAI])).toBeNull();
  });

  it("falls back to the listed models when the backend sends no prefixes", () => {
    const legacy = { name: "xai", available: true, models: [{ model: "grok-4.5" }] };
    expect(providerForModel("grok-4.5", [legacy])).toBe(legacy);
    expect(providerForModel("grok-9", [legacy])).toBeNull();
  });
});
