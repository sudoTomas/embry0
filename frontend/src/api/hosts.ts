import { api } from "./client";

export type HostTransport = "tailnet" | "ssh" | "http";
export type HostSource = "manual" | "tailnet";
export type HostStatus = "active" | "unreachable" | "removed";
export type LogSourceKind = "journald" | "docker" | "loki";

export interface HostLogSource {
  kind: LogSourceKind;
  /** A systemd unit for journald, a container name for docker, a LogQL selector for loki. */
  selector: string;
}

export interface HostRepoLink {
  repo: string;
  role: string;
}

/** A machine that runs code embry0 maintains (ADR 0013, docs/hosts.md). */
export interface Host {
  id: string;
  name: string;
  address: string;
  transport: HostTransport;
  roles: string[];
  log_source: HostLogSource | null;
  tags: string[];
  source: HostSource;
  last_seen_at: string | null;
  status: HostStatus;
  created_at: string | null;
  updated_at: string | null;
  repos: HostRepoLink[];
}

export interface HostList {
  hosts: Host[];
  transports: HostTransport[];
  sources: HostSource[];
  statuses: HostStatus[];
  log_source_kinds: LogSourceKind[];
}

/** The editable record: POST /hosts takes all of it, PATCH /hosts/{id} any part. */
export interface HostWrite {
  name: string;
  address: string;
  transport: HostTransport;
  roles: string[];
  log_source: HostLogSource | null;
  tags: string[];
  repos: HostRepoLink[];
}

export type HostPatch = Partial<HostWrite> & { status?: "active" | "removed" };

export interface HostProbe {
  host_id: string;
  transport: HostTransport;
  reachable: boolean;
  detail: string;
  checked_at: string;
  last_seen_at: string | null;
  device: Record<string, unknown> | null;
}

export interface TailnetImportResult {
  tailnet: string;
  credential_id: string | null;
  results: { name: string; host_id: string | null; outcome: string; detail: string }[];
  counts: { created: number; updated: number; unchanged: number; skipped: number };
}

export interface HostsFilter {
  source: HostSource | undefined;
  includeRemoved: boolean;
}

export async function fetchHosts(filter: HostsFilter): Promise<HostList> {
  const params: Record<string, string | boolean> = {};
  if (filter.source) params.source = filter.source;
  if (filter.includeRemoved) params.include_removed = true;
  const { data } = await api.get<HostList>("/hosts", { params });
  return data;
}

/** Add a host (admin). A taken name is a 409 and a misfit address a 422, both shown in the form. */
export async function createHost(body: HostWrite): Promise<Host> {
  const { data } = await api.post<Host>("/hosts", body, { suppressValidationToast: true });
  return data;
}

export async function updateHost(hostId: string, patch: HostPatch): Promise<Host> {
  const { data } = await api.patch<Host>(`/hosts/${encodeURIComponent(hostId)}`, patch, { suppressValidationToast: true });
  return data;
}

/** Mark the host removed; the row stays so a tailnet import does not bring it back. */
export async function removeHost(hostId: string): Promise<void> {
  await api.delete(`/hosts/${encodeURIComponent(hostId)}`);
}

/** Is the host reachable over its transport? Writes its status and last-seen time. Operator-tier. */
export async function probeHost(hostId: string): Promise<{ probe: HostProbe; host: Host }> {
  const { data } = await api.post<{ probe: HostProbe; host: Host }>(`/hosts/${encodeURIComponent(hostId)}/probe`, {}, { suppressValidationToast: true });
  return data;
}

/** One host per device of the tailnet, created or updated by name (admin). */
export async function importTailnetHosts(): Promise<TailnetImportResult> {
  const { data } = await api.post<TailnetImportResult>("/hosts/import/tailnet", { confirm: true }, { suppressValidationToast: true });
  return data;
}
