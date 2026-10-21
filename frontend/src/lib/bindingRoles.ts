import type { BindingRole, CredentialKind, ScopeKind } from "./types/credentials";

/**
 * The role a credential binds with, by its kind, and the scopes each role is
 * looked up at: KIND_FOR_ROLE and SCOPES_FOR_ROLE in
 * embry0/storage/repositories/credentials.py, which refuse any other pair
 * with 422. lib/__tests__/bindingRoleParity.test.ts pins both to the backend.
 */
export const ROLE_FOR_KIND: Record<CredentialKind, BindingRole> = {
  forge_token: "forge",
  llm_oauth: "llm",
  llm_api_key: "llm",
  ssh_key: "deploy",
  tailnet_api_key: "tailnet",
};

export const SCOPES_FOR_ROLE: Record<BindingRole, readonly ScopeKind[]> = {
  forge: ["repo", "owner", "host", "global"],
  llm: ["repo", "owner", "global"],
  deploy: ["host"],
  tailnet: ["global"],
};
