import { useState } from "react";
import { ChevronDown, ChevronUp, Workflow } from "lucide-react";
import { IconBox } from "@/components/ui/IconBox";
import { StateInspector } from "./StateInspector";
import { PipelineTree } from "@/components/pipeline-tree";
import { FlowConnector } from "@/components/pipeline-tree/FlowConnector";
import type { NodeStateEvent } from "@/lib/types";

interface StateFlowPanelProps {
  nodeStates: Record<string, NodeStateEvent>;
  phases: { agentType: string; agents: string[] }[];
  jobData?: {
    repo?: string;
    task?: string;
    totalCost?: number;
    prUrl?: string | null;
  };
  onAgentClick?: (agentType: string, nodeId: string) => void;
}

export function StateFlowPanel({ nodeStates, phases, jobData, onAgentClick }: StateFlowPanelProps) {
  const [stateExpanded, setStateExpanded] = useState(true);

  const totalAgents = phases.reduce((sum, p) => sum + p.agents.length, 0);
  const completedAgents = Object.values(nodeStates).filter((ns) => ns.state === "completed").length;
  const runningAgent = Object.values(nodeStates).find((ns) => ns.state === "running");
  const hasActivity = completedAgents > 0 || !!runningAgent;

  // Build state fields from available data
  const stateFields = [
    { name: "repo", type: "str", value: jobData?.repo ?? null },
    { name: "task", type: "str", value: jobData?.task ? (jobData.task.length > 20 ? jobData.task.slice(0, 20) + "..." : jobData.task) : null },
    { name: "agent_outputs", type: "list[dict]", value: completedAgents > 0 ? `[${completedAgents} items]` : null },
    { name: "total_cost", type: "float", value: jobData?.totalCost != null ? `$${jobData.totalCost.toFixed(3)}` : null },
    { name: "validation", type: "dict | None", value: null },
    { name: "pr_url", type: "str | None", value: jobData?.prUrl ?? null },
  ];

  const activeField = runningAgent ? "agent_outputs" : undefined;

  return (
    <div className="legion-card p-6">
      {/* Header */}
      <div className="flex items-center justify-between mb-5">
        <div className="flex items-center gap-3">
          <IconBox icon={Workflow} color="#06b6d4" size="lg" />
          <div>
            <h2 className="text-base font-bold text-cyan-400">LangGraph State Flow</h2>
            <p className="text-xs text-white/40">Watch data flow through the pipeline</p>
          </div>
        </div>
        <div className="flex gap-2">
          <span className="text-xs font-semibold px-2.5 py-0.5 rounded-md bg-cyan-500/10 border border-cyan-500/20 text-cyan-400">
            {stateFields.length} fields
          </span>
          <span className="text-xs font-semibold px-2.5 py-0.5 rounded-md bg-purple-500/10 border border-purple-500/20 text-purple-400">
            {totalAgents} agents
          </span>
        </div>
      </div>

      {/* State Inspector (collapsible) */}
      <div className="mb-4">
        <button
          onClick={() => setStateExpanded(!stateExpanded)}
          className="flex items-center gap-1 text-xs text-white/40 hover:text-white/60 transition-colors mb-2"
        >
          {stateExpanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
          State Inspector
        </button>
        {stateExpanded && (
          <StateInspector fields={stateFields} activeField={activeField} />
        )}
      </div>

      {/* Flow connector */}
      <FlowConnector active={hasActivity} />

      {/* Pipeline tree */}
      <div className="mt-2">
        <PipelineTree phases={phases} nodeStates={nodeStates} onAgentClick={onAgentClick} />
      </div>
    </div>
  );
}
