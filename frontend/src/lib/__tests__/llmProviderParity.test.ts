// The LLM providers the credential screens offer, pinned to the backend's own
// lists: a provider added there fails here instead of going missing from the
// dashboard, and one removed there is not offered and then refused on save.
import { describe, expect, it } from "vitest";
import catalogPy from "../../../../embry0/agents/providers.py?raw";
import registryPy from "../../../../embry0/services/credentials/providers.py?raw";
import schemaPy from "../../../../embry0/api/schemas/credentials.py?raw";
import {
  ALWAYS_ON_LLM_PROVIDERS,
  ENABLE_SETTING,
  LLM_PROVIDERS,
  OPENAI_COMPATIBLE_PROVIDERS,
  llmProviderStates,
} from "../llmProviders";

function capture(source: string, pattern: RegExp, what: string): string {
  const found = pattern.exec(source)?.[1];
  if (found === undefined) throw new Error(`${what} not found`);
  return found;
}

/** Providers POST /credentials takes that are not LLM providers. */
const NOT_LLM = ["github", "gitlab", "tailscale"];

describe("LLM providers match the backend", () => {
  it("lists every deployment's providers as the registry's LLM_PROVIDERS", () => {
    const body = capture(
      registryPy,
      /^LLM_PROVIDERS: dict\[str, LlmProvider\] = \{\n([\s\S]*?)\n\}/m,
      "LLM_PROVIDERS in embry0/services/credentials/providers.py",
    );
    expect([...ALWAYS_ON_LLM_PROVIDERS]).toEqual([...body.matchAll(/^\s+"([a-z]+)":/gm)].map((m) => m[1]));
  });

  it("lists the OpenAI-compatible family in the catalog's order", () => {
    const constants = capture(
      catalogPy,
      /^OPENAI_COMPATIBLE: tuple\[ModelProvider, \.\.\.\] = \(([^)]*)\)/m,
      "OPENAI_COMPATIBLE in embry0/agents/providers.py",
    )
      .split(",")
      .map((name) => name.trim())
      .filter(Boolean);
    const names = constants.map((constant) =>
      capture(
        catalogPy,
        new RegExp(`^${constant} = ModelProvider\\(\\n\\s+name="([a-z]+)"`, "m"),
        `${constant} in embry0/agents/providers.py`,
      ),
    );
    expect([...OPENAI_COMPATIBLE_PROVIDERS]).toEqual(names);
  });

  it("offers exactly the LLM providers POST /credentials accepts", () => {
    const literal = capture(
      schemaPy,
      /class CredentialCreate\(BaseModel\):[\s\S]*?provider: Literal\[([\s\S]*?)\]/,
      "CredentialCreate.provider in embry0/api/schemas/credentials.py",
    );
    const accepted = [...literal.matchAll(/"([a-z]+)"/g)].map((m) => m[1]);
    expect([...LLM_PROVIDERS].sort()).toEqual(accepted.filter((name) => !NOT_LLM.includes(name)).sort());
  });

  it("names the setting that enables the family", () => {
    expect(ENABLE_SETTING).toBe(
      capture(catalogPy, /^ENABLE_SETTING: Final = "([A-Z_]+)"/m, "ENABLE_SETTING in embry0/agents/providers.py"),
    );
  });

  it("sends a custom provider's API root under the metadata key the schema takes", () => {
    expect(
      capture(schemaPy, /unknown = sorted\(set\(metadata\) - \{"([a-z_]+)"\}\)/, "_custom_base_url's metadata key"),
    ).toBe("base_url");
  });
});

describe("llmProviderStates", () => {
  const states = (catalog: { name: string }[] | undefined) =>
    Object.fromEntries(llmProviderStates(catalog).map(({ name, state }) => [name, state]));

  it("enables a family member the catalog lists and names the rest not enabled", () => {
    expect(states([{ name: "anthropic" }, { name: "xai" }, { name: "openai" }])).toEqual({
      anthropic: "enabled",
      xai: "enabled",
      openai: "enabled",
      openrouter: "not_enabled",
      google: "not_enabled",
      groq: "not_enabled",
      deepseek: "not_enabled",
      custom: "not_enabled",
    });
  });

  it("claims nothing about the family before the catalog answers", () => {
    const before = states(undefined);
    expect(before.anthropic).toBe("enabled");
    expect(before.xai).toBe("enabled");
    for (const name of OPENAI_COMPATIBLE_PROVIDERS) expect(before[name]).toBe("unknown");
  });
});
