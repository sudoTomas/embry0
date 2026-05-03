export type EnvVarScope = "app" | "qa";

export interface EnvVar {
  key: string;
  value: string;
  var_type: "config" | "secret";
  description: string;
  required?: boolean;
  /**
   * Scope partition for the environment variable.
   *  - "app": application config and secrets (default), injected into all sandboxes.
   *  - "qa":  QA test credentials, only injected when qa_active=True.
   * Optional for backwards compat with rows persisted before Task 11.
   */
  scope?: EnvVarScope;
}

export interface DetectedEnvVar {
  key: string;
  default_value: string | null;
  description: string;
  suggested_type: "config" | "secret";
  is_configured: boolean;
  source: "repo" | "global" | null;
}
