import { XCircle, Clock, DollarSign, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/Button";
import type { NodeStateEvent } from "@/lib/types";

interface ErrorSummaryProps {
  errorMessage?: string | null;
  totalCost: number;
  startedAt?: string | null;
  finishedAt?: string | null;
  nodeStates: Record<string, NodeStateEvent>;
  onRetry?: () => void;
}

export function ErrorSummary({
  errorMessage,
  totalCost,
  startedAt,
  finishedAt,
  nodeStates,
  onRetry,
}: ErrorSummaryProps) {
  const failedAgent = Object.values(nodeStates).find((ns) => ns.state === "failed");

  const duration = startedAt && finishedAt
    ? Math.floor((new Date(finishedAt).getTime() - new Date(startedAt).getTime()) / 1000)
    : 0;
  const durationStr = duration >= 60
    ? `${Math.floor(duration / 60)}m ${duration % 60}s`
    : `${duration}s`;

  return (
    <div
      className="rounded-2xl p-6"
      style={{
        background: "linear-gradient(135deg, rgba(239,68,68,0.05), rgba(9,9,11,0.98))",
        border: "1px solid rgba(239,68,68,0.2)",
        boxShadow: "0 0 20px rgba(239,68,68,0.05)",
      }}
    >
      <div className="flex items-center gap-3 mb-4">
        <div className="w-10 h-10 rounded-[10px] flex items-center justify-center bg-red-500/12 border border-red-500/25">
          <XCircle className="w-5 h-5 text-red-500" />
        </div>
        <div className="flex-1">
          <h3 className="text-lg font-bold text-red-400">Job Failed</h3>
          {failedAgent && (
            <p className="text-sm text-white/40">
              Failed at: {failedAgent.agent_type.charAt(0).toUpperCase() + failedAgent.agent_type.slice(1)}
            </p>
          )}
        </div>
        {onRetry && (
          <Button variant="outline" size="sm" onClick={onRetry} className="gap-1.5">
            <RotateCcw className="w-3.5 h-3.5" />
            Retry
          </Button>
        )}
      </div>

      {errorMessage && (
        <div className="mb-4 px-3 py-2 rounded-lg bg-red-500/5 border border-red-500/10 text-sm text-red-300 font-mono">
          {errorMessage}
        </div>
      )}

      <div className="flex items-center gap-6 text-sm text-white/50">
        <div className="flex items-center gap-1.5">
          <DollarSign className="w-3.5 h-3.5" />
          <span className="font-mono">${totalCost.toFixed(3)}</span>
        </div>
        <div className="flex items-center gap-1.5">
          <Clock className="w-3.5 h-3.5" />
          <span>{durationStr}</span>
        </div>
      </div>
    </div>
  );
}
