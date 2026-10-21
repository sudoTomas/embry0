import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import {
  fetchSandboxProfiles,
  fetchSandboxProfile,
  createSandboxProfile,
  updateSandboxProfile,
  deleteSandboxProfile,
  resetSandboxProfile,
} from "@/api/sandbox-profiles";
import type { SandboxProfile } from "@/api/sandbox-profiles";

export function useSandboxProfiles() {
  return useQuery({ queryKey: ["sandbox-profiles"], queryFn: fetchSandboxProfiles });
}

export function useSandboxProfile(name: string | null) {
  return useQuery({
    queryKey: ["sandbox-profile", name],
    queryFn: () => fetchSandboxProfile(name!),
    enabled: !!name,
  });
}

/** A write to one profile stales both the list and that profile's form query
 * (["sandbox-profile", name]), so a reopened form shows what was saved. */
function invalidateProfile(qc: QueryClient, name: string) {
  qc.invalidateQueries({ queryKey: ["sandbox-profiles"] });
  qc.invalidateQueries({ queryKey: ["sandbox-profile", name] });
}

export function useCreateSandboxProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (profile: SandboxProfile) => createSandboxProfile(profile),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sandbox-profiles"] }),
  });
}

export function useUpdateSandboxProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, ...profile }: SandboxProfile) => updateSandboxProfile(name, { name, ...profile }),
    onSuccess: (_data, { name }) => invalidateProfile(qc, name),
  });
}

export function useDeleteSandboxProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => deleteSandboxProfile(name),
    onSuccess: (_data, name) => invalidateProfile(qc, name),
  });
}

export function useResetSandboxProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => resetSandboxProfile(name),
    onSuccess: (_data, name) => invalidateProfile(qc, name),
  });
}
