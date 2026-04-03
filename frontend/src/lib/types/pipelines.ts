export interface NodePosition {
  x: number;
  y: number;
}

export interface SandboxConfig {
  image?: string | null;
  environment?: Record<string, string>;
}

export interface AgentNode {
  node_id: string;
  agent_type: string;
  label?: string;
  position: NodePosition;
  model?: string | null;
  max_budget_usd?: number | null;
  max_turns?: number | null;
  effort?: string | null;
  tools?: string[] | null;
  skills?: string[] | null;
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

export interface PipelineTemplate {
  template_id: string;
  name: string;
  description: string;
  graph: PipelineGraph;
  created_at: string;
}

export interface PipelineTemplateSummary {
  template_id: string;
  name: string;
  description: string;
  created_at: string;
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

export interface SkillConfig {
  name: string;
  mode: "autonomous" | "guided" | "interactive";
  auto_answer_categories?: string[] | null;
  escalation_categories?: string[] | null;
  context_hints?: Record<string, string> | null;
}
