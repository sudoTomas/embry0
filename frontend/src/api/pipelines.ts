import { api } from "./client";
import {
  DEFAULT_TEMPLATE_BEHAVIOUR,
  type PipelineGraph,
  type PipelineTemplate,
  type PipelineTemplateSummary,
  type TemplateBehaviour,
  type TemplateKind,
} from "@/lib/types";

/**
 * The embry0 pipeline-templates API uses snake_case fields that don't quite
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
  /** Older rows and responses leave it out; the UI reads null either way. */
  sandbox_profile?: string | null;
  is_builtin?: boolean;
  default_for_kind?: string | null;
  /** #167; absent on an older backend. */
  fixed_route?: boolean;
  offered_to_triage?: boolean;
  job_kind?: string | null;
  delivers?: string;
  leader_agent?: string | null;
  created_at: string;
  updated_at: string;
}

/** What the row says about its jobs; a field an older backend leaves out reads as its default. */
function behaviourOf(row: ApiTemplateSummary): TemplateBehaviour {
  const d = DEFAULT_TEMPLATE_BEHAVIOUR;
  return {
    fixed_route: row.fixed_route ?? d.fixed_route,
    offered_to_triage: row.offered_to_triage ?? d.offered_to_triage,
    job_kind: (row.job_kind as TemplateKind | null | undefined) ?? d.job_kind,
    delivers: row.delivers ?? d.delivers,
    leader_agent: row.leader_agent ?? d.leader_agent,
  };
}

interface ApiTemplate extends ApiTemplateSummary {
  /** Its JSON text when it is too deeply nested to send as an object (#431). */
  graph_definition: PipelineGraph | string;
  /** Create and update responses only. */
  warnings?: string[];
}

function mapSummary(row: ApiTemplateSummary): PipelineTemplateSummary {
  return {
    template_id: row.id,
    name: row.name,
    description: row.description,
    created_at: row.created_at,
    sandbox_profile: row.sandbox_profile ?? null,
    is_builtin: row.is_builtin === true,
    default_for_kind: (row.default_for_kind as TemplateKind | null | undefined) ?? null,
    behaviour: behaviourOf(row),
  };
}

function mapTemplate(row: ApiTemplate): PipelineTemplate {
  return {
    template_id: row.id,
    name: row.name,
    description: row.description,
    graph: typeof row.graph_definition === "string" ? null : row.graph_definition,
    graph_text: typeof row.graph_definition === "string" ? row.graph_definition : null,
    created_at: row.created_at,
    warnings: row.warnings,
    is_builtin: row.is_builtin === true,
    default_for_kind: (row.default_for_kind as TemplateKind | null | undefined) ?? null,
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

/** A copy of a template under a new name (its graph and sandbox profile; the description is the copy's own). */
export async function duplicateTemplate(templateId: string, name: string): Promise<PipelineTemplate> {
  const { data } = await api.post<ApiTemplate>(`/pipelines/templates/${templateId}/duplicate`, { name });
  return mapTemplate(data);
}

/**
 * Make a template the default route for a kind, or clear it with null. The
 * null is sent on purpose: the route reads an absent field as "unchanged".
 * A kind another template holds is a 409 the caller shows.
 */
export async function setTemplateDefaultKind(templateId: string, kind: TemplateKind | null): Promise<PipelineTemplate> {
  const { data } = await api.put<ApiTemplate>(`/pipelines/templates/${templateId}`, { default_for_kind: kind });
  return mapTemplate(data);
}

/**
 * Write what a template's row says about its jobs (#167). Every field is sent:
 * job_kind's and leader_agent's null clears them.
 */
export async function setTemplateBehaviour(templateId: string, behaviour: TemplateBehaviour): Promise<PipelineTemplate> {
  const { data } = await api.put<ApiTemplate>(`/pipelines/templates/${templateId}`, behaviour);
  return mapTemplate(data);
}
