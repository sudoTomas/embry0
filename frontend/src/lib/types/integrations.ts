export interface IntegrationConfig {
  trigger_labels: string[];
  webhook_secret_set: boolean;
  slack_webhook_url_set: boolean;
  slack_webhook_url_masked: string;
  telegram_bot_token_set: boolean;
  telegram_bot_token_masked: string;
  telegram_chat_id: string;
}

export interface IntegrationConfigUpdate {
  trigger_labels?: string[];
  webhook_secret?: string;
  slack_webhook_url?: string;
  telegram_bot_token?: string;
  telegram_chat_id?: string;
}
