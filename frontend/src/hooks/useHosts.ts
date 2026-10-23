import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createHost,
  fetchHost,
  fetchHosts,
  importTailnetHosts,
  probeHost,
  removeHost,
  updateHost,
  type HostPatch,
  type HostWrite,
  type HostsFilter,
} from "@/api/hosts";

export function useHosts(filter: HostsFilter) {
  return useQuery({ queryKey: ["hosts", filter.source ?? "all", filter.includeRemoved], queryFn: () => fetchHosts(filter) });
}

export function useHost(hostId: string | null) {
  return useQuery({ queryKey: ["hosts", "one", hostId], queryFn: () => fetchHost(hostId!), enabled: !!hostId });
}

function invalidateHosts(qc: ReturnType<typeof useQueryClient>) {
  return qc.invalidateQueries({ queryKey: ["hosts"] });
}

export function useCreateHost() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (body: HostWrite) => createHost(body), onSuccess: () => invalidateHosts(qc) });
}

export function useUpdateHost() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: HostPatch }) => updateHost(id, patch),
    onSuccess: () => invalidateHosts(qc),
  });
}

export function useRemoveHost() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (id: string) => removeHost(id), onSuccess: () => invalidateHosts(qc) });
}

/** A probe writes the host's status, so the list is read again whatever it found. */
export function useProbeHost() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (id: string) => probeHost(id), onSettled: () => invalidateHosts(qc) });
}

export function useImportTailnetHosts() {
  const qc = useQueryClient();
  return useMutation({ mutationFn: () => importTailnetHosts(), onSettled: () => invalidateHosts(qc) });
}
