import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchSandboxProfiles,
  fetchSandboxProfile,
  createSandboxProfile,
  updateSandboxProfile,
  deleteSandboxProfile,
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
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sandbox-profiles"] }),
  });
}

export function useDeleteSandboxProfile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (name: string) => deleteSandboxProfile(name),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["sandbox-profiles"] }),
  });
}
