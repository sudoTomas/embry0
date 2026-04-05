export type OverrunMode = "soft" | "hard";

export interface ConfigResponse {
  max_budget_per_job_usd: number;
  daily_cap_usd: number;
  monthly_cap_usd: number;
  rate_limit_per_author_per_hour: number;
  overrun_mode: OverrunMode;
}

export type ConfigUpdateRequest = Partial<ConfigResponse>;
