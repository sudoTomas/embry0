import type { PipelinePhase } from "@/components/pipeline-tree";

/**
 * Default pipeline phases for the issue-to-PR workflow.
 * Used when no pipeline_graph is available from the job data.
 *
 * Each phase contains an agentType (matching PIPELINE_PHASES in constants)
 * and a list of agent node IDs within that phase.
 */
export const DEFAULT_ISSUE_TO_PR_PHASES: PipelinePhase[] = [
  { agentType: "triage", agents: ["triage"] },
  { agentType: "developer", agents: ["developer"] },
  { agentType: "validator", agents: ["validator"] },
  { agentType: "reviewer", agents: ["reviewer"] },
  { agentType: "output", agents: ["output"] },
];

/**
 * Extract pipeline phases from a pipeline graph definition.
 * Falls back to default phases if graph is unavailable.
 */
export function getPipelinePhases(
  pipelineGraph?: Record<string, unknown> | null,
): PipelinePhase[] {
  if (!pipelineGraph || !pipelineGraph.nodes) {
    return DEFAULT_ISSUE_TO_PR_PHASES;
  }

  // Try to extract phases from graph nodes
  try {
    const nodes = pipelineGraph.nodes as Array<{ node_id: string; agent_type: string }>;
    const phaseMap = new Map<string, string[]>();

    for (const node of nodes) {
      const agentType = node.agent_type ?? "output";
      if (!phaseMap.has(agentType)) {
        phaseMap.set(agentType, []);
      }
      phaseMap.get(agentType)!.push(node.node_id);
    }

    return Array.from(phaseMap.entries()).map(([agentType, agents]) => ({
      agentType,
      agents,
    }));
  } catch {
    return DEFAULT_ISSUE_TO_PR_PHASES;
  }
}
