import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  discoverOrganization,
  dismissNeed,
  fetchContextAudit,
  fetchOrganization,
  fetchOrganizationNeeds,
  fetchOrganizationRepos,
  fetchOrganizationRuns,
  fetchOrganizations,
  fetchOrganizationWebhook,
  installOrganizationWebhook,
  patchOrganization,
  reopenNeed,
  reprobeOrganization,
  setOrganizationStatus,
  updateOrganization,
  type DiscoveryRun,
  type Organization,
  type OrgPatch,
  type OrgSettingsPatch,
  type OrgStatus,
} from "@/api/orgs";

/** While a discovery run is going, its progress is read this often. */
export const RUN_POLL_MS = 1_500;

export function useOrganizations() {
  return useQuery({ queryKey: ["orgs"], queryFn: fetchOrganizations });
}

export function useOrganization(owner: string) {
  return useQuery({
    queryKey: ["orgs", owner],
    queryFn: () => fetchOrganization(owner),
    enabled: !!owner,
    retry: false,
  });
}

/** Runs, newest first; polls while the newest one is running. */
export function useOrganizationRuns(owner: string, enabled = true) {
  return useQuery({
    queryKey: ["orgs", owner, "runs"],
    queryFn: () => fetchOrganizationRuns(owner),
    enabled: !!owner && enabled,
    refetchInterval: (query) => {
      const runs = query.state.data as DiscoveryRun[] | undefined;
      return runs?.[0]?.status === "running" ? RUN_POLL_MS : false;
    },
  });
}

export function useOrganizationRepos(owner: string, enabled = true) {
  return useQuery({
    queryKey: ["orgs", owner, "repos"],
    queryFn: () => fetchOrganizationRepos(owner),
    enabled: !!owner && enabled,
  });
}

/** `data === null` means the server has no needs endpoint yet. */
export function useOrganizationNeeds(owner: string, enabled = true) {
  return useQuery({
    queryKey: ["orgs", owner, "needs"],
    queryFn: () => fetchOrganizationNeeds(owner),
    enabled: !!owner && enabled,
  });
}

export function useDiscoverOrganization(owner: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => discoverOrganization(owner),
    onSettled: () => qc.invalidateQueries({ queryKey: ["orgs", owner, "runs"] }),
  });
}

/** Dismiss or reopen one need; the list is read again either way. */
export function useNeedStatus(owner: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, dismiss }: { id: string; dismiss: boolean }) =>
      dismiss ? dismissNeed(owner, id) : reopenNeed(owner, id),
    onSettled: () => qc.invalidateQueries({ queryKey: ["orgs", owner, "needs"] }),
  });
}

/** Change an owner's settings; the stored row the server answers replaces the cached one. */
export function usePatchOrganizationSettings(owner: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (settings: OrgSettingsPatch) => patchOrganization(owner, settings),
    onSuccess: (org) => qc.setQueryData(["orgs", owner], org),
    onSettled: () => qc.invalidateQueries({ queryKey: ["orgs"] }),
  });
}

/** The organization's webhook as the forge reports it; read only for a GitHub owner on a tab that shows it. */
export function useOrganizationWebhook(owner: string, enabled = true) {
  return useQuery({
    queryKey: ["orgs", owner, "webhook"],
    queryFn: () => fetchOrganizationWebhook(owner),
    enabled: !!owner && enabled,
    retry: false,
  });
}

function replaceOrganization(qc: ReturnType<typeof useQueryClient>, owner: string) {
  return {
    onSuccess: (org: Organization) => qc.setQueryData(["orgs", owner], org),
    onSettled: () => qc.invalidateQueries({ queryKey: ["orgs"] }),
  };
}

export function useReprobeOrganization(owner: string) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: () => reprobeOrganization(owner), ...replaceOrganization(qc, owner) });
}

export function useUpdateOrganization(owner: string) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (body: OrgPatch) => updateOrganization(owner, body), ...replaceOrganization(qc, owner) });
}

export function useSetOrganizationStatus(owner: string) {
  const qc = useQueryClient();
  return useMutation({ mutationFn: (status: OrgStatus) => setOrganizationStatus(owner, status), ...replaceOrganization(qc, owner) });
}

/** Install the organization's webhook; the hook's read and the needs list (a missing hook is a need) are stale afterwards. */
export function useInstallOrganizationWebhook(owner: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => installOrganizationWebhook(owner),
    onSettled: () =>
      Promise.all([
        qc.invalidateQueries({ queryKey: ["orgs", owner, "webhook"] }),
        qc.invalidateQueries({ queryKey: ["orgs", owner, "needs"] }),
      ]),
  });
}

/** The owner's context audit as last read: each repository with instruction files and its newest audit. */
export function useContextAudit(owner: string, enabled = true) {
  return useQuery({
    queryKey: ["orgs", owner, "context-audit"],
    queryFn: () => fetchContextAudit(owner),
    enabled: !!owner && enabled,
  });
}

/** Reads every default branch again and shows that answer in place of the last one. */
export function useRefreshContextAudit(owner: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => fetchContextAudit(owner, true),
    onSuccess: (survey) => qc.setQueryData(["orgs", owner, "context-audit"], survey),
  });
}
