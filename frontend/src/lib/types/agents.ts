export interface AgentFieldInfo {
  name: string;
  description: string;
}

export interface AgentTypeInfo {
  type: string;
  description: string;
  phase: string;
  default_model: string;
  default_tools: string[];
  default_skills: string[];
  inputs: AgentFieldInfo[];
  outputs: AgentFieldInfo[];
  responsibilities: string[];
}

/** null = defer to the resolver's global default (embry0/agents/resolver.py). */
export type AgentExecutionMode = "sdk" | "cli";
export type AgentAuthMode = "api_key" | "oauth";

/** New format from GET /agents */
export interface AgentDefinition {
  type: string;
  description: string;
  model: string;
  tools: string[];
  skills: string[];
  system_prompt: string;
  execution_mode?: AgentExecutionMode | null;
  auth_mode?: AgentAuthMode | null;
  mcp_servers?: Record<string, unknown> | null;
  is_builtin: boolean;
  /** False for a definition no job runs: a brief writer's voice, listed under Background writers and
   * never a pipeline step, a job's per-agent model or a guild character. Absent on an older backend: true. */
  runs_in_jobs?: boolean;
  /** Catalog provider the model routes to by id prefix; null when none does.
   * Absent on a backend that predates the field. */
  provider?: string | null;
  /** The provider has a credential configured right now. */
  provider_ready?: boolean;
  /** The model a job that names none runs this agent on: the default model,
   * a per-job tier or a disabled-model stand-in can replace `model`. Absent on
   * a backend that predates the field (docs/providers.md). */
  effective_model?: string;
  /** What chose `effective_model`: `agent`, `default_model`,
   * `disabled_fallback`, `builtin_default` or `per_job_<tier>_tier`. */
  effective_model_source?: string;
  effective_provider?: string | null;
  effective_provider_ready?: boolean;
  /** Identity (docs/api.md, Agents); absent on a backend that predates it. */
  display_name?: string;
  avatar?: string;
  color?: string;
  tagline?: string;
  /** Catalog-shaped skills no source lists (`GET /agents/{type}` only). */
  orphan_skills?: string[];
  /** The skills triage may add to this agent for a job, each only from a source its skills
   * already load (docs/api.md, Agents). Absent on a backend that predates it. */
  triage_may_add_skills?: string[];
  /** Where the guild hall draws it (#167). Absent on a backend that predates it. */
  station?: string;
  /** The task the guild can give it: null none, "" a task naming no template (triage picks; a coding
   * agent's only), else that template (#167). Absent on a backend that predates it. */
  quest_template?: string | null;
  /** Read-only: whether the developer node runs it, so a guild task naming no template is its to take. */
  coding_agent?: boolean;
  /** Write responses only: skills accepted from a source that is not ready, and what a job runs without. */
  warnings?: string[];
  created_at?: string;
  updated_at?: string;
}

export interface AgentCreateRequest {
  type: string;
  description: string;
  model: string;
  tools?: string[];
  skills?: string[];
  system_prompt?: string;
  execution_mode?: AgentExecutionMode | null;
  auth_mode?: AgentAuthMode | null;
  mcp_servers?: Record<string, unknown>;
  display_name?: string;
  avatar?: string;
  color?: string;
  tagline?: string;
  triage_may_add_skills?: string[];
  station?: string;
  quest_template?: string | null;
}

/** PUT semantics (RAV-1058): omitted fields stay untouched; an explicitly-sent
 * null CLEARS the column (backend uses exclude_unset). */
export interface AgentUpdateRequest {
  description?: string;
  model?: string;
  tools?: string[];
  skills?: string[];
  system_prompt?: string;
  execution_mode?: AgentExecutionMode | null;
  auth_mode?: AgentAuthMode | null;
  mcp_servers?: Record<string, unknown> | null;
  /** Not nullable on the backend: a null here is a 422. */
  display_name?: string;
  avatar?: string;
  color?: string;
  tagline?: string;
  /** Not nullable on the backend: [] lets triage add none. */
  triage_may_add_skills?: string[];
  /** Not nullable on the backend either. */
  station?: string;
  /** null takes the agent's task out of the guild. */
  quest_template?: string | null;
}
