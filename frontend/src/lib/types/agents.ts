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
