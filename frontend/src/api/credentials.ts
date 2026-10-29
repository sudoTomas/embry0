import { api } from "./client";
import type {
  Binding,
  BindingInput,
  BindingRole,
  Credential,
  CredentialCreate,
  CredentialUpdate,
  ImportEnvReport,
  RepoCredentials,
  ResolveResult,
  ScopeKind,
  XaiDeviceLoginPoll,
  XaiDeviceLoginStarted,
} from "@/lib/types/credentials";
import type { LlmProviderName } from "@/lib/llmProviders";

export async function fetchCredentials(): Promise<Credential[]> {
  const { data } = await api.get<Credential[]>("/credentials");
  return data;
}

export async function createCredential(body: CredentialCreate): Promise<Credential> {
  // Modal renders the 422 inline. Response is CredentialOut: masked_preview, never the secret.
  const { data } = await api.post<Credential>("/credentials", body, {
    suppressValidationToast: true,
  });
  return data;
}

export async function updateCredential(id: string, body: CredentialUpdate): Promise<Credential> {
  const { data } = await api.put<Credential>(`/credentials/${id}`, body);
  return data;
}

export async function rotateCredential(id: string, secret: string): Promise<Credential> {
  const { data } = await api.post<Credential>(
    `/credentials/${id}/rotate`,
    { secret },
    { suppressValidationToast: true },
  );
  return data;
}

export async function verifyCredential(id: string): Promise<Credential> {
  const { data } = await api.post<Credential>(`/credentials/${id}/verify`);
  return data;
}

export async function deleteCredential(id: string): Promise<void> {
  await api.delete(`/credentials/${id}`);
}

export async function resolveCredential(
  repo: string,
  role: BindingRole = "forge",
): Promise<ResolveResult> {
  const { data } = await api.get<ResolveResult>("/credentials/resolve", {
    params: { repo, role },
  });
  return data;
}

export async function fetchScopedBindings(
  scope_kind: ScopeKind,
  scope_value: string,
): Promise<Binding[]> {
  const { data } = await api.get<{ bindings: Binding[] }>("/credentials/bindings", {
    params: { scope_kind, scope_value },
  });
  return data.bindings;
}

export async function setScopedBindings(
  scope_kind: Exclude<ScopeKind, "repo">,
  scope_value: string,
  bindings: BindingInput[],
): Promise<Binding[]> {
  // ScopedBindingsSet puts scope in the body and rejects scope_kind "repo".
  // Repo bindings go through /repos/{repo}/credentials. The drawer renders a
  // 422 detail inline, so the global validation toast would duplicate it.
  const { data } = await api.put<{ bindings: Binding[] }>(
    "/credentials/bindings",
    { scope_kind, scope_value, bindings },
    { suppressValidationToast: true },
  );
  return data.bindings;
}

export async function fetchRepoCredentials(fullRepo: string): Promise<RepoCredentials> {
  // Slash stays in the path (`acme/widgets`); the route is `{full_repo:repo}`.
  const { data } = await api.get<RepoCredentials>(`/repos/${fullRepo}/credentials`);
  return data;
}

export async function setRepoCredentials(
  fullRepo: string,
  bindings: BindingInput[],
): Promise<RepoCredentials> {
  const { data } = await api.put<RepoCredentials>(`/repos/${fullRepo}/credentials`, { bindings });
  return data;
}

export async function startXaiDeviceLogin(label: string): Promise<XaiDeviceLoginStarted> {
  const { data } = await api.post<XaiDeviceLoginStarted>(
    "/credentials/xai/device-login",
    { label },
    { suppressValidationToast: true },
  );
  return data;
}

export async function pollXaiDeviceLogin(loginId: string): Promise<XaiDeviceLoginPoll> {
  const { data } = await api.get<XaiDeviceLoginPoll>(`/credentials/xai/device-login/${loginId}`);
  return data;
}

export async function importEnvCredentials(replace: boolean): Promise<ImportEnvReport> {
  const { data } = await api.post<ImportEnvReport>("/credentials/import-env", { replace });
  return data;
}

/**
 * A provider's key from its LLM Providers card: stored, then made the provider's global
 * binding (in place of the global binding it had), so every job without a repository
 * binding runs on it. Two calls the Credentials page also makes, in one step.
 */
export async function addProviderKey(
  provider: LlmProviderName,
  body: { secret: string; label: string; baseUrl?: string },
): Promise<Credential> {
  const created = await createCredential({
    kind: "llm_api_key",
    provider,
    label: body.label,
    secret: body.secret,
    ...(body.baseUrl ? { metadata: { base_url: body.baseUrl } } : {}),
  });
  const current = await fetchScopedBindings("global", "");
  const kept = current
    .filter((b) => !(b.role === "llm" && b.provider === provider))
    .map((b) => ({ credential_id: b.credential_id, role: b.role, pinned: b.pinned, priority: b.priority }));
  await setScopedBindings("global", "", [...kept, { credential_id: created.id, role: "llm" }]);
  return created;
}
