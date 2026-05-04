import { api } from "./client";
import type { PipelineGraph, PipelineTemplate, PipelineTemplateSummary } from "@/lib/types";

/**
 * The Athanor pipeline-templates API uses snake_case fields that don't quite
 * match the frontend's existing types: it returns `id`/`graph_definition`
 * where the UI uses `template_id`/`graph`. Rather than rename across every
 * consumer (PipelinesPage, TemplateDrawer, TemplatePicker, PipelineEditor,
 * the templates type), we map at this boundary so the rest of the app keeps
 * its existing shape.
 *
 * Likewise, the list endpoint returns a bare array (`[...]`) — earlier code
 * read `data.templates` from a `{templates: [...]}` envelope that no longer
 * exists. Mapping centralized here.
 */

interface ApiTemplateSummary {
  id: string;
  name: string;
  description: string;
  sandbox_profile: string | null;
  is_builtin?: boolean;
  created_at: string;
  updated_at: string;
}

interface ApiTemplate extends ApiTemplateSummary {
  graph_definition: PipelineGraph;
  agent_models?: Record<string, string>;
}

function mapSummary(row: ApiTemplateSummary): PipelineTemplateSummary {
  return {
    template_id: row.id,
    name: row.name,
    description: row.description,
    created_at: row.created_at,
  };
}

function mapTemplate(row: ApiTemplate): PipelineTemplate {
  return {
    template_id: row.id,
    name: row.name,
    description: row.description,
    graph: row.graph_definition,
    created_at: row.created_at,
  };
}

export async function fetchTemplates(): Promise<PipelineTemplateSummary[]> {
  const { data } = await api.get<ApiTemplateSummary[]>("/pipelines/templates");
  return data.map(mapSummary);
}

export async function fetchTemplate(templateId: string): Promise<PipelineTemplate> {
  const { data } = await api.get<ApiTemplate>(`/pipelines/templates/${templateId}`);
  return mapTemplate(data);
}

export async function createTemplate(
  name: string,
  description: string,
  graph: PipelineGraph,
): Promise<PipelineTemplate> {
  const { data } = await api.post<ApiTemplate>("/pipelines/templates", {
    name,
    description,
    graph_definition: graph,
  });
  return mapTemplate(data);
}

export async function deleteTemplate(templateId: string): Promise<void> {
  await api.delete(`/pipelines/templates/${templateId}`);
}

export async function renameTemplate(
  templateId: string,
  name?: string,
  description?: string,
  graph?: PipelineGraph,
): Promise<PipelineTemplate> {
  const body: Record<string, unknown> = {};
  if (name !== undefined) body.name = name;
  if (description !== undefined) body.description = description;
  if (graph !== undefined) body.graph_definition = graph;
  const { data } = await api.put<ApiTemplate>(`/pipelines/templates/${templateId}`, body);
  return mapTemplate(data);
}

export async function validatePipeline(
  graph: PipelineGraph,
): Promise<{ valid: boolean; errors: string[] }> {
  const { data } = await api.post<{ valid: boolean; errors: string[] }>(
    "/pipelines/validate",
    graph,
  );
  return data;
}
