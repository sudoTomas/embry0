import { api } from "./client";

export interface ActiveSandbox {
  name: string;
  status: string;
  running_for: string;
}

export interface ActiveSandboxesResponse {
  containers: ActiveSandbox[];
  count: number;
}

export async function getActiveSandboxes(
  includeStopped = false,
): Promise<ActiveSandboxesResponse> {
  const { data } = await api.get<ActiveSandboxesResponse>("/sandboxes/active", {
    params: includeStopped ? { include_stopped: true } : {},
  });
  return data;
}
