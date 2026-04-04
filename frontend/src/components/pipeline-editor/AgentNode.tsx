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
      className="rounded-xl min-w-[180px] bg-[#0f1419] transition-all duration-200 hover:-translate-y-0.5"
      style={{
        border: `1.5px solid ${selected ? `${color}E6` : `${color}50`}`,
        boxShadow: selected
          ? `0 0 24px ${color}26, 0 4px 16px rgba(0,0,0,0.4)`
          : `0 2px 8px rgba(0,0,0,0.3)`,
      }}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!w-2 !h-2 !bg-[#0f1419]"
        style={{ border: `2px solid ${color}` }}
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
        <div className="flex items-center gap-1.5 mt-1.5 text-[10px] text-white/35">
          {shortModel && <span>{shortModel}</span>}
          {budget && <span>· ${budget}</span>}
          {!shortModel && !budget && <span>defaults</span>}
        </div>
      </div>

      <Handle
        type="source"
        position={Position.Right}
        className="!w-2 !h-2 !bg-[#0f1419]"
        style={{ border: `2px solid ${color}` }}
      />
    </div>
  );
}
