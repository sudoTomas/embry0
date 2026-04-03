import type { ReactNode } from "react";
import { getPhaseForAgent } from "@/lib/constants";

interface PhaseContainerProps {
  agentType: string;
  nodeCount: number;
  children: ReactNode;
}

export function PhaseContainer({ agentType, nodeCount, children }: PhaseContainerProps) {
  const phase = getPhaseForAgent(agentType);
  return (
    <div className="rounded-xl p-4" style={{ border: `1px solid ${phase.color}20` }}>
      <div className="flex justify-between mb-3">
        <div className="flex items-baseline gap-2">
          <span className="text-[11px] font-bold tracking-wider"
            style={{ color: agentType === "output" ? phase.color : "rgba(255,255,255,0.4)" }}>
            {phase.label}
          </span>
          <span className="text-[11px] text-white/20">— {phase.description}</span>
        </div>
        <span className="text-[11px] text-white/30">{nodeCount} {nodeCount === 1 ? "node" : "nodes"}</span>
      </div>
      <div className="flex flex-wrap gap-2.5 justify-center">{children}</div>
    </div>
  );
}
