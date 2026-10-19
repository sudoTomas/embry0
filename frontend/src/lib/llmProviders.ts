/**
 * The LLM providers a credential can be for: exactly the ones POST /credentials
 * accepts (embry0/api/schemas/credentials.py), in the backend catalog's order.
 * lib/__tests__/llmProviderParity.test.ts pins these lists to the backend's.
 *
 * Anthropic and xAI are in every deployment. The OpenAI-compatible family is off
 * until the deployment names a provider in OPENAI_COMPATIBLE_PROVIDERS
 * (docs/providers.md); until then the backend refuses its credentials.
 */
export const ALWAYS_ON_LLM_PROVIDERS = ["anthropic", "xai"] as const;
export const OPENAI_COMPATIBLE_PROVIDERS = ["openai", "openrouter", "google", "groq", "deepseek", "custom"] as const;
export const LLM_PROVIDERS = [...ALWAYS_ON_LLM_PROVIDERS, ...OPENAI_COMPATIBLE_PROVIDERS] as const;

export type LlmProviderName = (typeof LLM_PROVIDERS)[number];

/** The orchestrator setting that enables an OpenAI-compatible provider. */
export const ENABLE_SETTING = "OPENAI_COMPATIBLE_PROVIDERS";

export function isLlmProvider(name: string): name is LlmProviderName {
  return (LLM_PROVIDERS as readonly string[]).includes(name);
}

/** `unknown` until the catalog answers; the backend then decides on save. */
export type LlmProviderState = "enabled" | "not_enabled" | "unknown";

/**
 * Each LLM provider with whether this deployment takes its credentials.
 * GET /config/models lists the providers the deployment offers, the same set
 * POST /credentials accepts (both add the enabled family to Anthropic and xAI),
 * so a family member the catalog does not list is not enabled. With no catalog
 * (still loading, or the request failed) the family is unknown.
 */
export function llmProviderStates(
  catalog: readonly { name: string }[] | undefined,
): { name: LlmProviderName; state: LlmProviderState }[] {
  const listed = catalog ? new Set(catalog.map((provider) => provider.name)) : null;
  return LLM_PROVIDERS.map((name) => {
    if ((ALWAYS_ON_LLM_PROVIDERS as readonly string[]).includes(name)) return { name, state: "enabled" };
    if (listed === null) return { name, state: "unknown" };
    return { name, state: listed.has(name) ? "enabled" : "not_enabled" };
  });
}

/**
 * The LLM providers a repository's Accounts card gives a row: each enabled
 * provider the catalog marks available (it has an account somewhere), plus
 * any provider this repository has a binding for or resolves an account for,
 * so a provider that is bound or resolving is never hidden. Before the
 * catalog answers, availability is unknown and Anthropic and xAI keep their
 * rows, as llmProviderStates keeps them enabled.
 */
export function usableLlmProviders(
  catalog: readonly { name: string; available?: boolean }[] | undefined,
  bindings: readonly { role: string; provider?: string | null }[],
  effectiveLlm: Readonly<Partial<Record<string, unknown>>> | undefined,
): LlmProviderName[] {
  const available = catalog
    ? new Set(catalog.filter((provider) => provider.available === true).map((provider) => provider.name))
    : null;
  const bound = new Set(bindings.filter((binding) => binding.role === "llm").map((binding) => binding.provider));
  return llmProviderStates(catalog)
    .filter(
      ({ name, state }) =>
        bound.has(name) ||
        effectiveLlm?.[name] != null ||
        (state === "enabled" && (available === null || available.has(name))),
    )
    .map(({ name }) => name);
}
