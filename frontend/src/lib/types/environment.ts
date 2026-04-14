export interface EnvVar {
  key: string;
  value: string;
  var_type: "config" | "secret";
  description: string;
  required?: boolean;
}

export interface DetectedEnvVar {
  key: string;
  default_value: string | null;
  description: string;
  suggested_type: "config" | "secret";
  is_configured: boolean;
  source: "repo" | "global" | null;
}
