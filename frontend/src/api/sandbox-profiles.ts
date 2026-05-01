import { api } from "./client";

export interface SandboxProfile {
  name: string;
  base_image: string;
  additional_packages: string[];
  setup_commands: string[];
  memory: string;
  cpus: string;
  pids_limit: number;
  cap_drop: string[];
  cap_add: string[];
  security_opt: string[];
  agent_timeout_seconds: number;
  container_timeout_seconds: number;
  description: string;
  dind_enabled: boolean;
  idle_timeout_seconds: number;
  extra_networks: string[];
  env_defaults: Record<string, string>;
  is_builtin: boolean;
  created_at?: string;
  updated_at?: string;
}

export async function fetchSandboxProfiles(): Promise<SandboxProfile[]> {
  const { data } = await api.get<SandboxProfile[]>("/sandbox-profiles");
  return data;
}

export async function fetchSandboxProfile(name: string): Promise<SandboxProfile> {
  const { data } = await api.get<SandboxProfile>(`/sandbox-profiles/${name}`);
  return data;
}

export async function createSandboxProfile(
  profile: Partial<SandboxProfile> & { name: string },
): Promise<{ name: string; status: string }> {
  const { data } = await api.post("/sandbox-profiles", profile);
  return data;
}

export async function updateSandboxProfile(
  name: string,
  profile: Omit<SandboxProfile, "created_at" | "updated_at">,
): Promise<{ name: string; status: string }> {
  const { data } = await api.put(`/sandbox-profiles/${name}`, profile);
  return data;
}

export async function deleteSandboxProfile(name: string): Promise<void> {
  await api.delete(`/sandbox-profiles/${name}`);
}

export async function resetSandboxProfile(name: string): Promise<SandboxProfile> {
  const { data } = await api.post<SandboxProfile>(`/sandbox-profiles/${name}/reset`);
  return data;
}
