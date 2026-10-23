import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createCredential,
  deleteCredential,
  fetchCredentials,
  fetchRepoCredentials,
  fetchScopedBindings,
  importEnvCredentials,
  resolveCredential,
  rotateCredential,
  setRepoCredentials,
  setScopedBindings,
  updateCredential,
  verifyCredential,
} from "@/api/credentials";
import type {
  BindingInput,
  BindingRole,
  CredentialCreate,
  CredentialUpdate,
  ScopeKind,
} from "@/lib/types/credentials";

/** The account list: the key and fetch every reader of it shares. */
export function credentialsQuery() {
  return { queryKey: ["credentials"], queryFn: fetchCredentials };
}

export function useCredentials() {
  return useQuery(credentialsQuery());
}

/** A repo's account bindings: the key and fetch every reader of them shares. */
export function repoCredentialsQuery(fullRepo: string) {
  return {
    queryKey: ["credentials", "repo", fullRepo],
    queryFn: () => fetchRepoCredentials(fullRepo),
    enabled: !!fullRepo,
  };
}

export function useRepoCredentials(fullRepo: string) {
  return useQuery(repoCredentialsQuery(fullRepo));
}

export function useScopedBindings(kind: ScopeKind, value: string) {
  return useQuery({
    queryKey: ["credentials", "bindings", kind, value],
    queryFn: () => fetchScopedBindings(kind, value),
    enabled: !!kind,
  });
}

export function useResolveCredential(repo: string, role: BindingRole = "forge") {
  return useQuery({
    queryKey: ["credentials", "resolve", repo, role],
    queryFn: () => resolveCredential(repo, role),
    enabled: !!repo,
  });
}

export function useCreateCredential() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: CredentialCreate) => createCredential(body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["credentials"] }),
  });
}

export function useUpdateCredential() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: CredentialUpdate }) => updateCredential(id, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["credentials"] }),
  });
}

export function useRotateCredential() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, secret }: { id: string; secret: string }) => rotateCredential(id, secret),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["credentials"] }),
  });
}

export function useVerifyCredential() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => verifyCredential(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["credentials"] }),
  });
}

export function useDeleteCredential() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => deleteCredential(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["credentials"] }),
  });
}

export function useImportEnvCredentials() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (replace: boolean) => importEnvCredentials(replace),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["credentials"] }),
  });
}

export function useSetRepoCredentials() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ fullRepo, bindings }: { fullRepo: string; bindings: BindingInput[] }) =>
      setRepoCredentials(fullRepo, bindings),
    onSuccess: (_data, { fullRepo }) => {
      qc.invalidateQueries({ queryKey: ["credentials"] });
      qc.invalidateQueries({ queryKey: ["credentials", "repo", fullRepo] });
      qc.invalidateQueries({ queryKey: ["repos", "detail", fullRepo] });
    },
  });
}

export function useSetScopedBindings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      scope_kind,
      scope_value,
      bindings,
    }: {
      scope_kind: Exclude<ScopeKind, "repo">;
      scope_value: string;
      bindings: BindingInput[];
    }) => setScopedBindings(scope_kind, scope_value, bindings),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["credentials"] }),
  });
}
