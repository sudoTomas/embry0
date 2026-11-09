import { api } from "./client";
import type { ActivityEntry } from "@/lib/types";

export interface AuditFilters {
  actor?: string;
  action?: string;
  since?: string; // ISO datetime
  limit?: number;
  offset?: number;
}

export interface AuditResponse {
  entries: ActivityEntry[];
  total: number;
}

export async function fetchAuditLog(filters: AuditFilters = {}): Promise<AuditResponse> {
  const { data } = await api.get<AuditResponse>("/audit", { params: filters });
  return data;
}

/** One action an audit row can carry, as the registry serves it. A retired name, which only older rows carry,
 * names the action that replaced it in `replaced_by` and has that action's label; a current one has null. */
export interface AuditActionEntry {
  action: string;
  label: string;
  replaced_by: string | null;
}

/** Every audit action with its label (`GET /audit/actions`): the current ones in the registry's order, then the
 * retired names. Served from the registry the writers are typed against, so it lists every action recorded. A
 * failure is the caller's to handle (each action reads in sentence case), so it raises no toast. */
export async function fetchAuditActions(): Promise<AuditActionEntry[]> {
  const { data } = await api.get<{ actions: AuditActionEntry[] }>("/audit/actions", { suppressErrorToast: true });
  return data.actions;
}
