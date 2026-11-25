// The LLM providers the credential screens offer come from the provider
// catalogue (GET /config/providers/catalog), not from a list here (#173): a
// provider a release or the deployment adds is offered with no dashboard
// change. What the dashboard still knows is pinned to the backend's.
import { describe, expect, it } from "vitest";
import catalogueApiPy from "../../../../embry0/api/v1/provider_catalogue.py?raw";
import repositoryPy from "../../../../embry0/storage/repositories/credentials.py?raw";
import schemaPy from "../../../../embry0/api/schemas/credentials.py?raw";
import * as llmProviders from "../llmProviders";
import { ENABLE_ROUTE, NOT_LLM_PROVIDERS, isLlmProvider, llmProviderStates, usableLlmProviders } from "../llmProviders";

function capture(source: string, pattern: RegExp, what: string): string {
  const found = pattern.exec(source)?.[1];
  if (found === undefined) throw new Error(`${what} not found`);
  return found;
}

describe("the providers the dashboard knows match the backend's", () => {
  it("names the providers that are not LLM providers as the backend does", () => {
    const tuple = capture(
      repositoryPy,
      /^NOT_LLM_PROVIDERS = \(([^)]*)\)/m,
      "NOT_LLM_PROVIDERS in embry0/storage/repositories/credentials.py",
    );
    expect([...NOT_LLM_PROVIDERS]).toEqual([...tuple.matchAll(/"([a-z]+)"/g)].map((m) => m[1]));
  });

  it("keeps no list of the LLM providers: the catalogue is the list", () => {
    const lists = Object.entries(llmProviders).filter(
      ([name, value]) => Array.isArray(value) && name !== "NOT_LLM_PROVIDERS",
    );
    expect(lists.map(([name]) => name)).toEqual([]);
    expect(schemaPy).not.toMatch(/provider: Literal\[/);
  });

  it("names the route that turns a provider on", () => {
    const path = capture(
      catalogueApiPy,
      /^@router\.put\("([^"]+)"/m,
      "the PUT route of embry0/api/v1/provider_catalogue.py",
    );
    expect(ENABLE_ROUTE).toBe(`PUT /api/v1${path}`);
  });

  it("sends a custom provider's API root under the metadata key the schema takes", () => {
    expect(
      capture(schemaPy, /unknown = sorted\(set\(metadata\) - \{"([a-z_]+)"\}\)/, "_custom_base_url's metadata key"),
    ).toBe("base_url");
  });
});

describe("llmProviderStates", () => {
  const states = (catalogue: { name: string; enabled: boolean }[] | undefined) =>
    llmProviderStates(catalogue).map(({ name, state }) => `${name}:${state}`);

  it("lists each provider of the catalogue in its order, a new one too", () => {
    expect(
      states([
        { name: "anthropic", enabled: true },
        { name: "openai", enabled: false },
        { name: "mistral", enabled: true },
      ]),
    ).toEqual(["anthropic:enabled", "openai:not_enabled", "mistral:enabled"]);
  });

  it("claims nothing before the catalogue answers", () => {
    expect(states(undefined)).toEqual([]);
  });
});

describe("isLlmProvider", () => {
  it("takes any provider but the forges', SSH's and the tailnet's", () => {
    expect(["anthropic", "mistral", "custom"].map(isLlmProvider)).toEqual([true, true, true]);
    expect(["github", "gitlab", "ssh", "tailscale"].map(isLlmProvider)).toEqual([false, false, false, false]);
  });
});

describe("usableLlmProviders", () => {
  const catalogue = [
    { name: "anthropic", enabled: true },
    { name: "xai", enabled: true },
    { name: "mistral", enabled: true },
    { name: "groq", enabled: false },
  ];

  it("gives a row to each enabled provider with an account somewhere", () => {
    const offered = [
      { name: "anthropic", available: true },
      { name: "xai", available: false },
      { name: "mistral", available: true },
    ];
    expect(usableLlmProviders(catalogue, offered, [], undefined)).toEqual(["anthropic", "mistral"]);
  });

  it("keeps every enabled provider before availability is known", () => {
    expect(usableLlmProviders(catalogue, undefined, [], undefined)).toEqual(["anthropic", "xai", "mistral"]);
  });

  it("never hides a provider the repository is bound to or resolves, even one the catalogue no longer lists", () => {
    const rows = usableLlmProviders(
      catalogue,
      [{ name: "anthropic", available: true }],
      [
        { role: "llm", provider: "groq" },
        { role: "llm", provider: "retired" },
        { role: "forge", provider: "github" },
      ],
      { xai: { id: "c1" } },
    );
    expect(rows).toEqual(["anthropic", "xai", "groq", "retired"]);
  });
});
