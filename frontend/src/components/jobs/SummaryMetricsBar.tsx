import { useState, useEffect } from "react";
import { Clock, DollarSign, Layers, Activity } from "lucide-react";
import type { NodeStateEvent } from "@/lib/types";

interface SummaryMetricsBarProps {
  nodeStates: Record<string, NodeStateEvent>;
  totalPhases: number;
  totalCost: number;
  startedAt?: string | null;
  isRunning: boolean;
}

export function SummaryMetricsBar({
  nodeStates,
  totalPhases,
  totalCost,
  startedAt,
  isRunning,
}: SummaryMetricsBarProps) {
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!isRunning) return;
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [isRunning]);

  const completedPhases = new Set(
    Object.values(nodeStates)
      .filter((ns) => ns.state === "completed")
      .map((ns) => ns.agent_type),
  ).size;

  const runningAgent = Object.values(nodeStates).find((ns) => ns.state === "running");

  const elapsed = startedAt
    ? Math.floor((Date.now() - new Date(startedAt).getTime()) / 1000)
    : 0;
  const elapsedStr = elapsed >= 60
    ? `${Math.floor(elapsed / 60)}m ${elapsed % 60}s`
    : `${elapsed}s`;

  const progressPercent = totalPhases > 0 ? (completedPhases / totalPhases) * 100 : 0;

  return (
    <div>
      {/* Progress bar */}
      <div className="h-[3px] bg-white/[0.06] rounded-full mb-3 overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-500"
          style={{
            width: `${progressPercent}%`,
            background: "linear-gradient(90deg, #06b6d4, #0ea5e9)",
          }}
        />
      </div>

      {/* Metrics */}
      <div className="flex items-center gap-6 text-xs">
        <div className="flex items-center gap-1.5 text-white/50">
          <Clock className="w-3.5 h-3.5" />
          <span className="font-mono">{isRunning ? elapsedStr : "—"}</span>
        </div>
        <div className="flex items-center gap-1.5 text-white/50">
          <DollarSign className="w-3.5 h-3.5" />
          <span className="font-mono">${totalCost.toFixed(3)}</span>
        </div>
        <div className="flex items-center gap-1.5 text-white/50">
          <Layers className="w-3.5 h-3.5" />
          <span>Phase {completedPhases}/{totalPhases}</span>
        </div>
        {runningAgent && (
          <div className="flex items-center gap-1.5 text-amber-400">
            <Activity className="w-3.5 h-3.5" />
            <span>{runningAgent.agent_type.charAt(0).toUpperCase() + runningAgent.agent_type.slice(1)}</span>
            <div className="legion-spinner w-3 h-3" style={{ borderWidth: 1.5, color: "#f59e0b" }} />
          </div>
        )}
      </div>
    </div>
  );
}
