/**
 * The LLM providers a credential can be for. The provider catalogue names them
 * (GET /config/providers/catalog, docs/providers.md), not the dashboard: one a
 * release or the deployment adds is offered with no change here (#173). A
 * provider the catalogue lists but does not enable is shown, not offered, since
 * POST /credentials refuses its key until the catalogue turns it on.
 *
 * Every provider that is not an LLM provider is one of NOT_LLM_PROVIDERS, as in
 * the backend (embry0/storage/repositories/credentials.py);
 * lib/__tests__/llmProviderParity.test.ts pins the two lists together.
 */
export const NOT_LLM_PROVIDERS = ["github", "gitlab", "ssh", "tailscale"] as const;

/** An LLM provider's name: whatever the provider catalogue calls it. */
export type LlmProviderName = string;

/** Where a provider is turned on: the provider catalogue's write (no restart). */
export const ENABLE_ROUTE = "PUT /api/v1/config/providers/catalog";

/** Whether a credential's provider is an LLM provider: any provider but the forges', SSH's and the tailnet's. */
export function isLlmProvider(name: string): boolean {
  return !(NOT_LLM_PROVIDERS as readonly string[]).includes(name);
}

export type LlmProviderState = "enabled" | "not_enabled";

/**
 * Each provider of the catalogue, in its order, with whether this deployment
 * takes its credentials. Before the catalogue answers (or when the request
 * failed) there is none: the backend decides on save.
 */
export function llmProviderStates(
  catalogue: readonly { name: string; enabled: boolean }[] | undefined,
): { name: LlmProviderName; state: LlmProviderState }[] {
  return (catalogue ?? []).map(({ name, enabled }) => ({ name, state: enabled ? "enabled" : "not_enabled" }));
}

/**
 * The LLM providers a repository's Accounts card gives a row: each provider
 * the catalogue enables that has an account somewhere (GET /config/models
 * marks it available), plus any provider this repository has a binding for or
 * resolves an account for, so a provider that is bound or resolving is never
 * hidden, even one the catalogue no longer lists. Before GET /config/models
 * answers, availability is unknown and every enabled provider keeps its row.
 */
export function usableLlmProviders(
  catalogue: readonly { name: string; enabled: boolean }[] | undefined,
  offered: readonly { name: string; available?: boolean }[] | undefined,
  bindings: readonly { role: string; provider?: string | null }[],
  effectiveLlm: Readonly<Partial<Record<string, unknown>>> | undefined,
): LlmProviderName[] {
  const available = offered
    ? new Set(offered.filter((provider) => provider.available === true).map((provider) => provider.name))
    : null;
  const bound = new Set(bindings.filter((binding) => binding.role === "llm").map((binding) => binding.provider));
  const rows = llmProviderStates(catalogue)
    .filter(
      ({ name, state }) =>
        bound.has(name) ||
        effectiveLlm?.[name] != null ||
        (state === "enabled" && (available === null || available.has(name))),
    )
    .map(({ name }) => name);
  const listed = new Set(rows);
  const elsewhere = [
    ...[...bound].filter((name): name is string => typeof name === "string"),
    ...Object.keys(effectiveLlm ?? {}).filter((name) => effectiveLlm?.[name] != null),
  ].filter((name) => isLlmProvider(name) && !listed.has(name) && !(catalogue ?? []).some((p) => p.name === name));
  return [...rows, ...[...new Set(elsewhere)]];
}
