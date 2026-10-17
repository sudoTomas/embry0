// RAV-1239: provider_mode/ollama_base_url removed — the backend never read
// them on any execution path; model tiers + credential status remain.
// RAV-1297: disabled_models (per-model enablement) rides the same row.
export interface ProviderConfig {
  model_heavy: string;
  model_medium: string;
  model_light: string;
  default_model: string;
  disabled_models: string[];
  api_key_set: boolean;
  oauth_token_set: boolean;
}

export interface ProviderConfigUpdate {
  model_heavy?: string;
  model_medium?: string;
  model_light?: string;
  default_model?: string;
  disabled_models?: string[];
}

export interface ConnectionTestResult {
  status: "ok" | "error";
  message: string;
}

// RAV-1297: the complete provider catalog from GET /config/models.
export interface CatalogModel {
  model: string;
  enabled: boolean;
  pricing_usd_per_mtok: { input: number | null; output: number | null };
}

export interface CatalogProvider {
  name: string;
  base_url: string;
  available: boolean;
  /** How the provider authenticates right now — null when unavailable. */
  credential_source: string | null;
  /** True for the runtime default provider (Anthropic). */
  default: boolean;
  /** Model id prefixes this provider serves. Absent on an older backend. */
  model_prefixes?: string[];
  /** The most recent "Test connection" outcome, null when never run.
   * Absent on an older backend. */
  last_probe?: ProviderLastProbe | null;
  models: CatalogModel[];
}

export interface ProviderLastProbe {
  /** A probe status word: ok, unauthorized, entitlement_refused, ... */
  status: string;
  /** ISO 8601 timestamp of the test. */
  checked_at: string;
}

export interface ModelsCatalog {
  heavy: string;
  medium: string;
  light: string;
  providers: CatalogProvider[];
}

// RAV-1297: live per-provider connection test.
export interface ProviderProbeCheck {
  source: string;
  status: string;
  message: string;
}

export interface ProviderProbeResult {
  provider: string;
  status: string;
  checks: ProviderProbeCheck[];
}
