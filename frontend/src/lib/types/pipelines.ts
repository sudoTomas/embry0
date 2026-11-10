import type { SkillsDelta } from "@/lib/skills";

export interface NodePosition {
  x: number;
  y: number;
}

export interface SandboxConfig {
  image?: string | null;
  environment?: Record<string, string>;
}

/** A template node's per-node overrides (backend `node_config`, route_plan.py). */
export interface AgentNodeConfig {
  model?: string | null;
  max_budget_usd?: number | null;
  max_turns?: number | null;
  effort?: string | null;
  tools?: string[] | null;
  skills?: SkillsDelta | string[] | null;
  prompt_prepend?: string | null;
  prompt_append?: string | null;
  custom_prompt?: string | null;
  custom_tools?: string[] | null;
  sandbox?: SandboxConfig | null;
}

export interface AgentNode {
  node_id: string;
  agent_type: string;
  label?: string;
  position: NodePosition;
  /**
   * RAV-1285: the physical LangGraph node whose node_started/node_completed
   * stream events represent this graph node. Present on the backend-emitted
   * `pipeline_graph` event; absent on editor/template graphs.
   */
  physical_node?: string;
  config?: AgentNodeConfig | null;
  /** Legacy (pre-#78) top-level overrides, read only; the editor writes `config`. */
  model?: string | null;
  max_budget_usd?: number | null;
  max_turns?: number | null;
  effort?: string | null;
  tools?: string[] | null;
  skills?: string[] | SkillsDelta | null;
  prompt_prepend?: string | null;
  prompt_append?: string | null;
  custom_prompt?: string | null;
  custom_tools?: string[] | null;
  sandbox?: SandboxConfig | null;
}

export type EdgeType = "flow" | "feedback";

export interface LoopConfig {
  max_loops?: number | null;
  max_loop_budget_usd?: number | null;
  feedback_mode?: "result" | "summary" | "diff";
}

export interface PipelineEdge {
  edge_id: string;
  source: string;
  target: string;
  edge_type: EdgeType;
  loop_config?: LoopConfig | null;
}

export interface PipelineMetadata {
  max_total_budget_usd: number;
  max_total_loops: number;
  created_by: "auto" | "manual" | "triage";
  template_id?: string | null;
}

export interface PipelineGraph {
  graph_id: string;
  name: string;
  description?: string;
  nodes: AgentNode[];
  edges: PipelineEdge[];
  metadata: PipelineMetadata;
}

/** The job kinds a template can be the default route for; at most one template per kind. */
export const TEMPLATE_KINDS = ["code", "research", "analysis", "ops"] as const;
export type TemplateKind = (typeof TEMPLATE_KINDS)[number];

/** What a template's row says about its jobs (#167, docs/api.md "Pipeline templates"). */
export interface TemplateBehaviour {
  /** A job that names it skips triage: its route is fixed. */
  fixed_route: boolean;
  /** Triage is offered it, and plan_route keeps triage's pick of it. */
  offered_to_triage: boolean;
  /** The kind its jobs are recorded as when it is no kind's default; null leaves triage's. */
  job_kind: TemplateKind | null;
  /** What its jobs hand back, in the person's words, for their briefs; "" says nothing. */
  delivers: string;
  /** The agent its jobs are drawn as before one starts, and credited to; null for none. */
  leader_agent: string | null;
}

/** A row from a backend that predates #167 behaves as one of the deployment's own did. */
export const DEFAULT_TEMPLATE_BEHAVIOUR: TemplateBehaviour = {
  fixed_route: false,
  offered_to_triage: true,
  job_kind: null,
  delivers: "",
  leader_agent: null,
};

export interface PipelineTemplate {
  template_id: string;
  name: string;
  description: string;
  /** None when the stored graph is too deeply nested for the API to send as an object: see ``graph_text``. */
  graph: PipelineGraph | null;
  /** The stored graph's JSON text when the API sent that instead of the graph (#431); shown read-only. */
  graph_text?: string | null;
  created_at: string;
  /** Write responses only: non-fatal problems the backend found (e.g. a skill whose source is not ready). */
  warnings?: string[];
  /** Seeded by embry0 and re-seeded on every restart, so an edit saved in
   * place does not survive one; Save As keeps it as a template of your own. */
  is_builtin?: boolean;
  default_for_kind?: TemplateKind | null;
}

export interface PipelineTemplateSummary {
  template_id: string;
  name: string;
  description: string;
  created_at: string;
  /** Seeded by embry0; Save As keeps a copy of your own. */
  is_builtin?: boolean;
  /** The kind this template is the default route for, or null. */
  default_for_kind?: TemplateKind | null;
  sandbox_profile?: string | null;
  /** What the row says about its jobs (#167); the list always sets it. */
  behaviour?: TemplateBehaviour;
}

export interface AgentTypeInfo {
  agent_type: string;
  description: string;
  default_tools: string[];
  default_model: string;
  default_effort: string;
  default_max_turns: number;
  default_max_budget_usd: number;
  available_skills: string[];
}
