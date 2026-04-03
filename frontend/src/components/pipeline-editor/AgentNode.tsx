import { Handle, Position, type NodeProps } from "@xyflow/react";
import { getAgentColor, getAgentCategory } from "@/lib/graph-utils";

export function AgentNode({ data, selected }: NodeProps) {
  const color = getAgentColor(data.agentType as string);
  const category = getAgentCategory(data.agentType as string);
  const label = (data.label as string) || (data.agentType as string);
  const tools = data.tools as string[] | undefined;
  const model = data.model as string | undefined;
  const budget = data.maxBudgetUsd as number | undefined;

  const shortModel = model?.replace("claude-", "").split("-")[0] ?? null;

  return (
    <div
      className="rounded-lg border-2 min-w-[180px] bg-[#12141f] transition-all duration-200"
      style={{
        borderColor: selected ? color : `${color}50`,
        boxShadow: selected
          ? `0 0 24px ${color}20, 0 4px 16px rgba(0,0,0,0.4)`
          : "0 2px 8px rgba(0,0,0,0.3)",
      }}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!w-3 !h-3 !border-2 !border-[#12141f]"
        style={{ background: color }}
      />

      {/* Gradient header strip */}
      <div
        className="h-1 rounded-t-[5px]"
        style={{ background: `linear-gradient(90deg, ${color}, ${color}30)` }}
      />

      <div className="px-3 py-2.5">
        <div className="flex items-center justify-between">
          <span
            className="text-[9px] uppercase tracking-widest font-semibold"
            style={{ color: `${color}BB` }}
          >
            {category}
          </span>
          {tools && tools.length > 0 && (
            <span className="text-[9px] bg-white/[0.06] rounded px-1.5 py-0.5 text-white/30">
              {tools.length} tools
            </span>
          )}
        </div>
        <div className="text-[13px] font-bold text-slate-100 mt-1">{label}</div>
        <div className="flex items-center gap-1.5 mt-1.5 text-[10px] text-white/30">
          {shortModel && <span>{shortModel}</span>}
          {budget && <span>· ${budget}</span>}
          {!shortModel && !budget && <span>defaults</span>}
        </div>
      </div>

      <Handle
        type="source"
        position={Position.Right}
        className="!w-3 !h-3 !border-2 !border-[#12141f]"
        style={{ background: color }}
      />
    </div>
  );
}
