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

/** New format from GET /agents */
export interface AgentDefinition {
  type: string;
  description: string;
  model: string;
  tools: string[];
  skills: string[];
  system_prompt: string;
  is_builtin: boolean;
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
}

export interface AgentUpdateRequest {
  description?: string;
  model?: string;
  tools?: string[];
  skills?: string[];
  system_prompt?: string;
}
