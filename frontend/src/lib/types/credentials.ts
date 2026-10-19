import type { LlmProviderName } from "../llmProviders";

export type CredentialKind = "forge_token" | "llm_oauth" | "llm_api_key" | "ssh_key" | "tailnet_api_key";
export type CredentialProvider = "github" | "gitlab" | "ssh" | "tailscale" | LlmProviderName;
export type CredentialStatus = "ok" | "disabled" | "quarantined";
export type BindingRole = "forge" | "llm" | "deploy" | "tailnet";
export type ScopeKind = "repo" | "owner" | "host" | "global";

export interface Credential {
  id: string;
  kind: CredentialKind;
  provider: CredentialProvider;
  host: string;
  label: string;
  git_author_name: string | null;
  git_author_email: string | null;
  masked_preview: string;
  verified_login: string | null;
  status: CredentialStatus;
  status_reason: string | null;
  quarantined_until: string | null;
  binding_count: number;
  created_at: string | null;
  updated_at: string | null;
  rotated_at: string | null;
  last_used_at: string | null;
}

/** `secret` is request-only; responses never echo it. xAI `llm_oauth` is not created here. */
export interface CredentialCreate {
  kind: "forge_token" | "llm_oauth" | "llm_api_key";
  provider: "github" | "gitlab" | LlmProviderName;
  host?: string;
  label: string;
  secret: string;
  git_author_name?: string | null;
  git_author_email?: string | null;
  /** A custom provider's key carries the root of its OpenAI-compatible API. */
  metadata?: { base_url: string };
}

export interface CredentialUpdate {
  label?: string;
  git_author_name?: string | null;
  git_author_email?: string | null;
  status?: "ok" | "disabled";
}

export interface Binding {
  id?: number;
  scope_kind: ScopeKind;
  scope_value: string;
  role: BindingRole;
  provider: CredentialProvider;
  credential_id: string;
  pinned: boolean;
  priority: number;
  label?: string;
  status?: CredentialStatus;
}

export interface BindingInput {
  credential_id: string;
  role: BindingRole;
  pinned?: boolean;
  priority?: number;
}

export interface ResolveResult {
  id: string | null;
  label: string;
  provider: string;
  host: string;
  source: "registry" | "env";
  trace: string[];
}

export interface ResolvedCredential {
  id: string | null;
  label: string;
  provider: string;
  source: string;
  trace: string[];
  host?: string;
}

export interface XaiDeviceLoginStarted {
  login_id: string;
  verification_uri_complete: string;
  user_code: string;
  expires_at: string;
  interval: number;
}

export interface XaiDeviceLoginPoll {
  status: "pending" | "expired" | "done";
  credential?: Credential | null;
}

export interface RepoCredentials {
  repo: string;
  bindings: Binding[];
  effective: {
    forge?: ResolvedCredential | { error: string };
    llm?: Partial<Record<LlmProviderName, ResolvedCredential | { error: string }>>;
  };
}

export interface ImportEnvReport {
  created: string[];
  bound: string[];
  skipped: string[];
}
