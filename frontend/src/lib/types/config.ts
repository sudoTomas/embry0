import type { ProviderMode } from "./jobs";

export interface ConfigResponse {
  max_budget_usd: number;
  daily_budget_cap_usd: number;
  monthly_budget_cap_usd: number;
  provider_mode: ProviderMode;
  default_model: string;
  sandbox_memory: string;
  sandbox_cpus: string;
  trigger_labels: string;
  rate_limit_per_author_per_hour: number;
  clone_repos_per_issue: boolean;
  slack_webhook_url: string;
  slack_webhook_url_configured: boolean;
  telegram_bot_token: string;
  telegram_bot_token_configured: boolean;
  telegram_chat_id: string;
}

export type ConfigUpdateRequest = Partial<ConfigResponse>;
