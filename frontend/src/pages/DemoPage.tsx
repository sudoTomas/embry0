import { useState } from "react";
import { Link } from "react-router";
import { ArrowLeft, FlaskConical } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { SummaryMetricsBar } from "@/components/jobs/SummaryMetricsBar";
import { AgentExecutionCard } from "@/components/agents/AgentExecutionCard";
import { AgentDetailPopup } from "@/components/agents/AgentDetailPopup";
import { StateFlowPanel } from "@/components/state/StateFlowPanel";
import { DEFAULT_ISSUE_TO_PR_PHASES } from "@/lib/pipeline-phases";
import { useAgentTypes } from "@/hooks/useAgentTypes";
import type { NodeStateEvent } from "@/lib/types";
import type { AgentTypeInfo } from "@/lib/types";

// Mock node states: triage=completed, developer=running, others=pending
const MOCK_NODE_STATES: Record<string, NodeStateEvent> = {
  triage: {
    type: "node_state",
    node_id: "triage",
    agent_type: "triage",
    state: "completed",
    iteration: 1,
    cost_usd: 0.042,
    turns: 3,
    duration_seconds: 18.4,
  },
  developer: {
    type: "node_state",
    node_id: "developer",
    agent_type: "developer",
    state: "running",
    iteration: 1,
    cost_usd: 0.187,
    turns: 12,
    duration_seconds: 94.1,
  },
  validator: {
    type: "node_state",
    node_id: "validator",
    agent_type: "validator",
    state: "pending",
    iteration: 0,
    cost_usd: 0,
    turns: 0,
    duration_seconds: 0,
  },
  reviewer: {
    type: "node_state",
    node_id: "reviewer",
    agent_type: "reviewer",
    state: "pending",
    iteration: 0,
    cost_usd: 0,
    turns: 0,
    duration_seconds: 0,
  },
  output: {
    type: "node_state",
    node_id: "output",
    agent_type: "output",
    state: "pending",
    iteration: 0,
    cost_usd: 0,
    turns: 0,
    duration_seconds: 0,
  },
};

const MOCK_JOB = {
  repo: "demo/example-repo",
  task: "Fix authentication bug in login handler",
  total_cost_usd: 0.229,
  started_at: new Date(Date.now() - 112 * 1000).toISOString(),
  pr_url: null,
};

const MOCK_LIVE_OUTPUT: Record<string, string> = {
  triage: "Analyzed issue #142: auth token not invalidated on logout",
  developer: "Patching session.destroy() callback in routes/auth.js...",
};

export function DemoPage() {
  const { data: agentTypes } = useAgentTypes();
  const [selectedAgent, setSelectedAgent] = useState<{ agentType: string; nodeId: string } | null>(null);

  const agentInfo: AgentTypeInfo | undefined = selectedAgent
    ? agentTypes?.find((a: AgentTypeInfo) => a.type === selectedAgent.agentType)
    : undefined;

  const handleAgentClick = (agentType: string, nodeId: string) => {
    setSelectedAgent({ agentType, nodeId });
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center gap-4">
        <Link to="/jobs">
          <Button variant="ghost" size="icon">
            <ArrowLeft className="h-4 w-4" />
          </Button>
        </Link>
        <div className="flex-1">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-bold">{MOCK_JOB.repo}</h1>
            <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-md text-xs font-semibold bg-orange-500/15 border border-orange-500/25 text-orange-400">
              <FlaskConical className="w-3 h-3" />
              Demo Mode
            </span>
          </div>
          <p className="text-sm text-muted-foreground font-mono">demo-job-00000000</p>
        </div>
      </div>

      {/* Task description */}
      <div className="px-4 py-3 rounded-lg bg-white/[0.03] border border-white/[0.06] text-sm text-white/70">
        {MOCK_JOB.task}
      </div>

      {/* Demo notice */}
      <div className="px-4 py-3 rounded-lg bg-orange-500/[0.06] border border-orange-500/[0.15] text-xs text-orange-400/80 flex items-center gap-2">
        <FlaskConical className="w-3.5 h-3.5 shrink-0" />
        This is a demo with simulated data. All agent states, metrics, and outputs shown here are mock values for preview purposes.
      </div>

      {/* Metrics bar */}
      <SummaryMetricsBar
        nodeStates={MOCK_NODE_STATES}
        totalPhases={DEFAULT_ISSUE_TO_PR_PHASES.length}
        totalCost={MOCK_JOB.total_cost_usd}
        startedAt={MOCK_JOB.started_at}
        isRunning={true}
      />

      {/* Two-panel layout */}
      <div className="grid grid-cols-1 xl:grid-cols-[420px_1fr] gap-5">
        {/* Left panel: Agent execution cards */}
        <div className="space-y-2.5">
          {DEFAULT_ISSUE_TO_PR_PHASES.map((phase) =>
            phase.agents.map((nodeId) => (
              <AgentExecutionCard
                key={nodeId}
                agentType={phase.agentType}
                nodeState={MOCK_NODE_STATES[nodeId]}
                liveOutput={MOCK_LIVE_OUTPUT[nodeId]}
                onClick={() => handleAgentClick(phase.agentType, nodeId)}
              />
            ))
          )}
        </div>

        {/* Right panel: State flow + pipeline tree */}
        <div className="overflow-y-auto max-h-[calc(100vh-300px)]">
          <StateFlowPanel
            nodeStates={MOCK_NODE_STATES}
            phases={DEFAULT_ISSUE_TO_PR_PHASES}
            jobData={{
              repo: MOCK_JOB.repo,
              task: MOCK_JOB.task,
              totalCost: MOCK_JOB.total_cost_usd,
              prUrl: MOCK_JOB.pr_url,
            }}
            onAgentClick={handleAgentClick}
          />
        </div>
      </div>

      {/* Agent detail popup */}
      {selectedAgent && (
        <AgentDetailPopup
          agentType={selectedAgent.agentType}
          agentInfo={agentInfo}
          nodeState={MOCK_NODE_STATES[selectedAgent.nodeId]}
          onClose={() => setSelectedAgent(null)}
        />
      )}
    </div>
  );
}
