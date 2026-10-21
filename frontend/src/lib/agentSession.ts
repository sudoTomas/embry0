import { resolveAgentType } from "@/lib/agentIdentity";

/**
 * The `agent_sessions` row a run's agent writes, keyed on (job_id, agent_type).
 * Every writer stores the row under the agent row's type: `developer`
 * (nodes/_developer.py), `review` (nodes/_review.py), `triage`
 * (nodes/_triage.py), `qa` (qa/nodes.py), `pr-reviewer` (pr_review/nodes.py)
 * and `onboarding` (onboard/nodes.py); generic_agent steps (research,
 * analysis, ops) keep no session. The one spelling that differs is the review
 * step, `reviewer` in templates and events and `review` in the row, which is
 * exactly the mapping resolveAgentType makes. The row is per agent type, not
 * per run: an agent's runs share it, their messages merged.
 */
export function sessionAgentType(agent: string): string {
  return resolveAgentType(agent);
}
