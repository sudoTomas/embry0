/** Structural slice of a GET /config/models provider entry that model routing needs. */
export interface RoutableProvider {
  name: string;
  available: boolean;
  /** Absent on a backend that predates the field. */
  model_prefixes?: string[];
  models: { model: string }[];
}

/**
 * The catalog provider that serves `model`: the first whose id prefix it carries,
 * the rule the backend routes by (embry0/services/provider_readiness.py). A
 * backend without `model_prefixes` falls back to the provider's listed models.
 * Null when nothing matches, a custom id included.
 */
export function providerForModel<P extends RoutableProvider>(model: string, providers: readonly P[]): P | null {
  if (!model) return null;
  for (const provider of providers) {
    const serves = provider.model_prefixes
      ? provider.model_prefixes.some((prefix) => model.startsWith(prefix))
      : provider.models.some((m) => m.model === model);
    if (serves) return provider;
  }
  return null;
}
