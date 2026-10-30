export type OverrunMode = "soft" | "hard";

/**
 * The fleet limits of the settings document's `budget` section that the
 * General tab edits (#171). For the four numbers, 0 is no limit.
 */
export interface BudgetLimits {
  max_budget_per_job_usd: number;
  daily_cap_usd: number;
  monthly_cap_usd: number;
  rate_limit_per_author_per_hour: number;
  overrun_mode: OverrunMode;
}
