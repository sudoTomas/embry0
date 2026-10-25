import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

interface ProviderModel {
  model: string;
  // RAV-1297: per-model enablement — disabled models stay out of every
  // selector. Optional so a pre-1297 backend (no flag) means enabled.
  enabled?: boolean;
  pricing_usd_per_mtok: { input: number | null; output: number | null };
  /** OpenRouter's live catalog: display name and maker. */
  name?: string;
  vendor?: string;
}

export interface ProviderCatalog {
  name: string;
  base_url: string;
  available: boolean;
  /** Model id prefixes the provider serves; absent on an older backend. */
  model_prefixes?: string[];
  models: ProviderModel[];
}

interface ModelsConfig {
  heavy: string;
  medium: string;
  light: string;
  providers?: ProviderCatalog[];
}

const NO_PROVIDERS: ProviderCatalog[] = [];

const FALLBACK_MODELS = [
  "claude-opus-5",
  "claude-sonnet-5",
  "claude-haiku-4-5",
];

export function useModels(): { models: string[]; providers: ProviderCatalog[]; isLoading: boolean } {
  const { data, isLoading } = useQuery({
    queryKey: ["config", "models"],
    queryFn: async () => {
      const { data } = await api.get<ModelsConfig>("/config/models");
      return data;
    },
    staleTime: 5 * 60 * 1000, // 5 minutes — model config rarely changes
    retry: 1,
  });

  // Memoized so the array is referentially stable across renders — consumers
  // (AgentFormPage) put it in useEffect deps; a fresh array per render sent
  // that effect into an infinite setState loop (RAV-1058).
  const models = useMemo(() => {
    if (!data) return FALLBACK_MODELS;
    // RAV-1238: the tier trio plus every model from AVAILABLE providers
    // (e.g. grok-4.5 when the xai key or OAuth proxy is live). Unavailable
    // providers stay hidden — offering a model that can't run just produces
    // failing jobs. RAV-1297: operator-disabled models stay out too; the
    // tier trio is always offered (the backend interlock keeps tiers
    // enabled).
    const providerModels = (data.providers ?? [])
      .filter((p) => p.available)
      .flatMap((p) => p.models.filter((m) => m.enabled !== false).map((m) => m.model));
    return [...new Set([data.heavy, data.medium, data.light, ...providerModels])];
  }, [data]);

  // The catalog itself, for consumers that name the provider of a model
  // (AgentFormPage). Empty until it loads or on a backend without it.
  const providers = data?.providers ?? NO_PROVIDERS;

  return { models, providers, isLoading };
}
