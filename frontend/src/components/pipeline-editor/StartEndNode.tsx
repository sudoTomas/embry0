import { Handle, Position, type NodeProps } from "@xyflow/react";

const ROLE_CONFIG = {
  start: { color: "#06b6d4", label: "START", handle: "source" as const },
  end: { color: "#f43f5e", label: "END", handle: "target" as const },
} as const;

export function StartEndNode({ data, selected }: NodeProps) {
  const role = (data.nodeRole as "start" | "end") ?? "start";
  const { color, label, handle } = ROLE_CONFIG[role];

  return (
    <div
      className="flex items-center justify-center rounded-full transition-all duration-200"
      style={{
        width: 100,
        height: 36,
        background: `${color}18`,
        border: `1.5px solid ${selected ? `${color}CC` : `${color}55`}`,
        boxShadow: selected
          ? `0 0 16px ${color}30, 0 2px 8px rgba(0,0,0,0.3)`
          : `0 1px 4px rgba(0,0,0,0.2)`,
      }}
    >
      {handle === "target" && (
        <Handle
          type="target"
          position={Position.Left}
          className="!w-2 !h-2 !bg-[#0f1419]"
          style={{ border: `2px solid ${color}` }}
        />
      )}

      <span
        className="text-[11px] font-bold tracking-wider select-none"
        style={{ color }}
      >
        {label}
      </span>

      {handle === "source" && (
        <Handle
          type="source"
          position={Position.Right}
          className="!w-2 !h-2 !bg-[#0f1419]"
          style={{ border: `2px solid ${color}` }}
        />
      )}
    </div>
  );
}
