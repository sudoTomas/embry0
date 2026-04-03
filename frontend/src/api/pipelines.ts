import { api } from "./client";
import type { PipelineGraph, PipelineTemplate, PipelineTemplateSummary } from "@/lib/types";

export async function fetchTemplates(): Promise<PipelineTemplateSummary[]> {
  const { data } = await api.get("/pipelines/templates");
  return data.templates;
}

export async function fetchTemplate(templateId: string): Promise<PipelineTemplate> {
  const { data } = await api.get(`/pipelines/templates/${templateId}`);
  return data;
}

export async function createTemplate(
  name: string,
  description: string,
  graph: PipelineGraph,
): Promise<PipelineTemplate> {
  const { data } = await api.post("/pipelines/templates", { name, description, graph });
  return data;
}

export async function deleteTemplate(templateId: string): Promise<void> {
  await api.delete(`/pipelines/templates/${templateId}`);
}

export async function renameTemplate(
  templateId: string,
  name?: string,
  description?: string,
  graph?: Record<string, unknown>,
): Promise<PipelineTemplate> {
  const { data } = await api.patch(`/pipelines/templates/${templateId}`, { name, description, graph });
  return data;
}

export async function validatePipeline(
  graph: Record<string, unknown>,
): Promise<{ valid: boolean; errors: string[] }> {
  const { data } = await api.post("/pipelines/validate", { graph });
  return data;
}
