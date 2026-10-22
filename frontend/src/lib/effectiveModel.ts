import type { AgentDefinition } from "@/lib/types/agents";

/**
 * The model, provider and readiness a job that names no model runs one agent
 * on, as GET /agents reports them (`effective_agent_model` in
 * embry0/agents/resolver.py), and what put that model in place of the stored
 * one. A backend without the `effective_*` fields gets the stored ones.
 */
export interface RunModel {
  model: string;
  provider: string | null;
  /** Undefined when the backend sends no readiness. */
  ready: boolean | undefined;
  /** What chose a model other than the agent's own; null when its own runs. */
  reason: string | null;
}

const TIER_SOURCE = /^per_job_(\w+)_tier$/;

/** Words for `effective_model_source`; null for `agent`, the agent's own model. */
export function runModelReason(source: string | undefined): string | null {
  if (!source || source === "agent") return null;
  if (source === "default_model") return "Deployment default model";
  if (source === "disabled_fallback") return "Stand-in for a disabled model";
  if (source === "builtin_default") return "Built-in default";
  const tier = TIER_SOURCE.exec(source)?.[1];
  if (tier) return `${tier.charAt(0).toUpperCase()}${tier.slice(1)} tier, named by every job`;
  return source.replace(/_/g, " ");
}

export function runModelOf(agent: AgentDefinition): RunModel {
  if (!agent.effective_model) {
    return { model: agent.model, provider: agent.provider ?? null, ready: agent.provider_ready, reason: null };
  }
  return {
    model: agent.effective_model,
    provider: agent.effective_provider ?? null,
    ready: agent.effective_provider_ready,
    reason: runModelReason(agent.effective_model_source),
  };
}

/**
 * What the agent form says when jobs will not run the model being edited, or
 * null. The default model and a per-job tier replace whatever is stored, so
 * they hold for any pick; a disabled-model stand-in holds only for the saved
 * model it was resolved for.
 */
export function runOverrideNote(agent: AgentDefinition | undefined, chosenModel: string): string | null {
  if (!agent?.effective_model) return null;
  const source = agent.effective_model_source;
  const model = agent.effective_model;
  if (source === "default_model") {
    return `Jobs run this agent on ${model}, the deployment default model, not on the model chosen here.`;
  }
  const tier = source ? TIER_SOURCE.exec(source)?.[1] : undefined;
  if (tier) return `Every job runs this agent on the ${tier} tier, now ${model}, not on the model chosen here.`;
  if (source === "disabled_fallback" && chosenModel === agent.model) {
    return `This model is disabled, so jobs run ${model} in its place.`;
  }
  return null;
}
