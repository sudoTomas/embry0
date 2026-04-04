import { X } from "lucide-react";
import { useEffect } from "react";
import { IconBox } from "@/components/ui/IconBox";
import { getPhaseForAgent } from "@/lib/constants";
import type { AgentDefinition } from "@/lib/types";
import type { NodeStateEvent } from "@/lib/types";
import { getAgentIcon } from "@/lib/agentIcons";

interface AgentDetailPopupProps {
  agentType: string;
  agentInfo?: AgentDefinition;
  nodeState?: NodeStateEvent;
  onClose: () => void;
}

export function AgentDetailPopup({
  agentType,
  agentInfo,
  nodeState,
  onClose,
}: AgentDetailPopupProps) {
  const phase = getPhaseForAgent(agentType);
  const Icon = getAgentIcon(agentType);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />

      {/* Modal */}
      <div
        role="dialog"
        aria-modal="true"
        className="relative w-full max-w-2xl max-h-[85vh] overflow-y-auto mx-4 rounded-2xl p-6"
        style={{
          background: "linear-gradient(135deg, rgba(15,20,25,0.98), rgba(9,9,11,0.99))",
          border: `1px solid ${phase.color}25`,
          boxShadow: `0 0 40px ${phase.color}10`,
        }}
      >
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <div className="flex items-center gap-3">
            <IconBox icon={Icon} color={phase.color} size="lg" />
            <div>
              <h3 className="text-lg font-bold" style={{ color: phase.color }}>
                {agentInfo?.type
                  ? agentInfo.type.charAt(0).toUpperCase() + agentInfo.type.slice(1)
                  : agentType.charAt(0).toUpperCase() + agentType.slice(1)}
              </h3>
              <p className="text-xs text-white/40">{phase.label} Phase</p>
            </div>
          </div>
          <button onClick={onClose} aria-label="Close" className="text-white/40 hover:text-white/80 transition-colors">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Description */}
        {agentInfo?.description && (
          <p className="text-sm text-white/70 mb-4">{agentInfo.description}</p>
        )}

        {/* Config badges */}
        <div className="flex flex-wrap gap-2 mb-3">
          {agentInfo?.model && (
            <div
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs"
              style={{ backgroundColor: `${phase.color}15`, border: `1px solid ${phase.color}25` }}
            >
              <span className="text-white/50">Model</span>
              <span className="font-semibold" style={{ color: phase.color }}>
                {agentInfo.model}
              </span>
            </div>
          )}
          {agentInfo?.tools && agentInfo.tools.length > 0 && (
            <div
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs"
              style={{ backgroundColor: `${phase.color}15`, border: `1px solid ${phase.color}25` }}
            >
              <span className="text-white/50">Tools</span>
              <span className="font-semibold" style={{ color: phase.color }}>
                {agentInfo.tools.join(", ")}
              </span>
            </div>
          )}
          {agentInfo?.skills && agentInfo.skills.length > 0 && (
            <div
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs"
              style={{ backgroundColor: `${phase.color}15`, border: `1px solid ${phase.color}25` }}
            >
              <span className="text-white/50">Skills</span>
              <span className="font-semibold" style={{ color: phase.color }}>
                {agentInfo.skills.join(", ")}
              </span>
            </div>
          )}
        </div>


        {/* Live metrics (only shown during execution) */}
        {nodeState && nodeState.state !== "pending" && (
          <div className="grid grid-cols-4 gap-3">
            {[
              { label: "Cost", value: `$${nodeState.cost_usd.toFixed(3)}` },
              { label: "Duration", value: `${nodeState.duration_seconds.toFixed(1)}s` },
              { label: "Turns", value: String(nodeState.turns) },
              { label: "Iteration", value: String(nodeState.iteration) },
            ].map((metric) => (
              <div key={metric.label} className="text-center p-2 rounded-lg bg-white/[0.03]">
                <p className="text-[10px] text-white/40">{metric.label}</p>
                <p className="text-sm font-mono font-semibold" style={{ color: phase.color }}>
                  {metric.value}
                </p>
              </div>
            ))}
          </div>
        )}

        <p className="text-center text-[10px] text-white/20 mt-4">Click anywhere outside to close</p>
      </div>
    </div>
  );
}
