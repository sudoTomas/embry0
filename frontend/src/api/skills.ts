import { api } from "./client";
import type {
  AgentDefinition,
  InlineSkill,
  SkillCatalogEntry,
  SkillSource,
  SkillSourceCreate,
  SkillSourceUpdate,
} from "@/lib/types";

/** The Skills routes of docs/api.md. Names and ids go into the path URL-encoded. */
const enc = encodeURIComponent;

export async function fetchSkills(): Promise<SkillCatalogEntry[]> {
  const { data } = await api.get<SkillCatalogEntry[]>("/skills");
  return data;
}

export async function fetchSkillSources(): Promise<SkillSource[]> {
  const { data } = await api.get<SkillSource[]>("/skills/sources");
  return data;
}

export async function createSkillSource(body: SkillSourceCreate): Promise<SkillSource> {
  const { data } = await api.post<SkillSource>("/skills/sources", body, { suppressValidationToast: true });
  return data;
}

export async function updateSkillSource(id: string, body: SkillSourceUpdate): Promise<SkillSource> {
  const { data } = await api.patch<SkillSource>(`/skills/sources/${enc(id)}`, body, { suppressValidationToast: true });
  return data;
}

export async function syncSkillSource(id: string): Promise<{ id: string; status: string }> {
  const { data } = await api.post<{ id: string; status: string }>(`/skills/sources/${enc(id)}/sync`);
  return data;
}

export async function deleteSkillSource(id: string): Promise<void> {
  await api.delete(`/skills/sources/${enc(id)}`);
}

export async function fetchInlineSkills(): Promise<InlineSkill[]> {
  const { data } = await api.get<InlineSkill[]>("/skills/inline");
  return data;
}

export async function putInlineSkill(
  name: string,
  body: { description: string; body: string },
): Promise<InlineSkill> {
  const { data } = await api.put<InlineSkill>(`/skills/inline/${enc(name)}`, body, { suppressValidationToast: true });
  return data;
}

export async function deleteInlineSkill(name: string): Promise<void> {
  await api.delete(`/skills/inline/${enc(name)}`);
}

export async function addAgentSkill(type: string, name: string): Promise<AgentDefinition> {
  const { data } = await api.post<AgentDefinition>(`/agents/${enc(type)}/skills/${enc(name)}`);
  return data;
}

export async function removeAgentSkill(type: string, name: string): Promise<AgentDefinition> {
  const { data } = await api.delete<AgentDefinition>(`/agents/${enc(type)}/skills/${enc(name)}`);
  return data;
}
