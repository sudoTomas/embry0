export type ProviderMode = "anthropic_api" | "claude_max" | "ollama";

export interface ProviderConfig {
  provider_mode: ProviderMode;
  model_heavy: string;
  model_medium: string;
  model_light: string;
  default_model: string;
  api_key_set: boolean;
  oauth_token_set: boolean;
  ollama_base_url: string;
}

export interface ProviderConfigUpdate {
  provider_mode?: ProviderMode;
  model_heavy?: string;
  model_medium?: string;
  model_light?: string;
  default_model?: string;
  ollama_base_url?: string;
}

export interface ConnectionTestResult {
  status: "ok" | "error";
  message: string;
}
