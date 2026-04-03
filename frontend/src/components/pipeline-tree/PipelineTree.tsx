import type { NodeStateEvent } from "@/lib/types";
import { PhaseContainer } from "./PhaseContainer";
import { TreeAgentNode, type AgentNodeState } from "./TreeAgentNode";
import { FlowConnector } from "./FlowConnector";
import { Workflow } from "lucide-react";
import { IconBox } from "@/components/ui/IconBox";

export interface PipelinePhase {
  agentType: string;
  agents: string[];
}

interface PipelineTreeProps {
  phases: PipelinePhase[];
  nodeStates: Record<string, NodeStateEvent>;
  onAgentClick?: (agentType: string, nodeId: string) => void;
  title?: string;
}

function getNodeState(nodeStates: Record<string, NodeStateEvent>, nodeId: string): AgentNodeState {
  const ns = nodeStates[nodeId];
  if (!ns) return "pending";
  if (ns.state === "completed") return "completed";
  if (ns.state === "running") return "running";
  if (ns.state === "failed") return "failed";
  return "pending";
}

function isPhaseActive(nodeStates: Record<string, NodeStateEvent>, agents: string[]): boolean {
  return agents.some((id) => {
    const state = getNodeState(nodeStates, id);
    return state === "running" || state === "completed";
  });
}

export function PipelineTree({ phases, nodeStates, onAgentClick, title }: PipelineTreeProps) {
  const totalNodes = phases.reduce((sum, p) => sum + p.agents.length, 0);

  return (
    <div>
      <div className="flex items-center justify-between mb-5">
        <div className="flex items-center gap-3">
          <IconBox icon={Workflow} color="#a855f7" size="lg" />
          <div>
            <h3 className="text-base font-bold text-purple-400">{title ?? "Issue-to-PR Pipeline"}</h3>
            <p className="text-xs text-white/40">LangGraph Orchestrated Pipeline</p>
          </div>
        </div>
        <div className="flex gap-2">
          <span className="text-xs font-semibold px-2.5 py-0.5 rounded-md bg-cyan-500/10 border border-cyan-500/20 text-cyan-400">
            {totalNodes} nodes
          </span>
          <span className="text-xs font-semibold px-2.5 py-0.5 rounded-md bg-purple-500/10 border border-purple-500/20 text-purple-400">
            {phases.length} phases
          </span>
        </div>
      </div>

      <div className="space-y-0">
        {phases.map((phase, idx) => {
          const prevActive = idx > 0 && isPhaseActive(nodeStates, phases[idx - 1].agents);
          return (
            <div key={phase.agentType}>
              {idx > 0 && <FlowConnector active={prevActive} />}
              <PhaseContainer agentType={phase.agentType} nodeCount={phase.agents.length}>
                {phase.agents.map((nodeId) => (
                  <TreeAgentNode
                    key={nodeId}
                    agentType={phase.agentType}
                    state={getNodeState(nodeStates, nodeId)}
                    onClick={() => onAgentClick?.(phase.agentType, nodeId)}
                  />
                ))}
              </PhaseContainer>
            </div>
          );
        })}
      </div>
    </div>
  );
}
