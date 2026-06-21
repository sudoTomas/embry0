import { Handle, Position, type NodeProps } from "@xyflow/react";
import type { AgentTaskStatus } from "@/api/agent";

type BlockedStatus = AgentTaskStatus | "selected";

// companion-status palette — kept local so the node is a self-contained read of
// `{ label, status }` data. Mirrors the table row colors below.
const STATUS_COLOR: Record<BlockedStatus, string> = {
  selected: "var(--color-primary)",
  running: "#06b6d4",
  queued: "#94a3b8",
  done: "#22c55e",
  failed: "#ef4444",
  dead_letter: "#a855f7",
};

/**
 * ReactFlow node for the blocked-by dependency graph.
 *
 * Typed as `NodeProps` (not a custom prop shape) so the component plugs into
 * ReactFlow's `nodeTypes` map without fighting the library's `data: unknown`
 * contract. The `data` payload is `BlockedNodeData` by construction
 * (`buildBlockedByGraph` is the only producer), so we read with narrow casts
 * — matching the AgentNode / StartEndNode pattern next door.
 */
export function BlockedNode({ data, selected }: NodeProps) {
  const label = (data as { label?: string }).label ?? "";
  const status = ((data as { status?: BlockedStatus }).status ?? "queued") as BlockedStatus;
  const color = STATUS_COLOR[status];
  const isRoot = status === "selected";

  return (
    <div
      className="rounded-md border bg-card px-3 py-2 text-xs shadow-sm select-none"
      style={{
        borderColor: color,
        outline: selected ? `1px solid ${color}` : undefined,
        minWidth: 140,
      }}
      data-status={status}
    >
      <Handle type="target" position={Position.Left} style={{ background: color }} />
      <div
        className="font-medium truncate"
        style={{ color: isRoot ? "var(--color-primary)" : undefined }}
      >
        {label}
      </div>
      <div className="mt-0.5 uppercase tracking-wider" style={{ color, fontSize: 9 }}>
        {status.replace("_", " ")}
      </div>
      <Handle type="source" position={Position.Right} style={{ background: color }} />
    </div>
  );
}
