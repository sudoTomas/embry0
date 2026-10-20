import { Handle, Position, type NodeProps } from "@xyflow/react";

const SPHERE_PX = 64;

const ROLE_CONFIG = {
  start: { label: "START", handle: "source" as const },
  end: { label: "END", handle: "target" as const },
} as const;

/**
 * Sentinel pipeline-graph node — circular sphere in the muted foreground,
 * never gold: a pipeline's start and end need no person, and gold marks
 * only what does. Renders as a smaller sibling of the agent sphere: ring +
 * cardinal dots + equator + a single pole accent (start = north pole,
 * end = south pole).
 *
 * §3.2 — sentinels sit on the equator, at least ±1.4R out, in circular layout.
 */
export function StartEndNode({ data, selected }: NodeProps) {
  const role = (data.nodeRole as "start" | "end") ?? "start";
  const { label, handle } = ROLE_CONFIG[role];
  const poleY = role === "start" ? 18 : 46;

  return (
    <div className="flex flex-col items-center gap-1 select-none text-fg-muted">
      <div
        className="relative transition-all duration-200 hover:-translate-y-0.5"
        style={{
          width: SPHERE_PX,
          height: SPHERE_PX,
        }}
      >
        {handle === "target" && (
          <Handle
            type="target"
            position={Position.Left}
            className="!w-2 !h-2 !bg-surface !border-2 !border-border-strong"
            style={{ top: "50%" }}
          />
        )}

        <svg
          className="absolute inset-0 w-full h-full"
          viewBox="0 0 64 64"
          aria-hidden="true"
        >
          <circle
            cx="32"
            cy="32"
            r="22"
            fill="none"
            stroke="currentColor"
            strokeWidth={selected ? "2.6" : "2"}
            opacity={selected ? "0.95" : "0.7"}
          />
          <circle cx="32" cy="10" r="2.4" fill="currentColor" />
          <circle cx="54" cy="32" r="2.4" fill="currentColor" />
          <circle cx="32" cy="54" r="2.4" fill="currentColor" />
          <circle cx="10" cy="32" r="2.4" fill="currentColor" />
          <line
            x1="14"
            y1="32"
            x2="50"
            y2="32"
            stroke="currentColor"
            strokeWidth="1.4"
            opacity="0.55"
          />
          <circle cx="32" cy={poleY} r="3.6" fill="currentColor" opacity="0.9" />
          {/* Selected: a thin ring in the node's own colour just outside its
              cardinal dots, never a glow. */}
          {selected && (
            <circle
              data-testid="node-selected-ring"
              cx="32"
              cy="32"
              r="27"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.5"
            />
          )}
        </svg>

        {handle === "source" && (
          <Handle
            type="source"
            position={Position.Right}
            className="!w-2 !h-2 !bg-surface !border-2 !border-border-strong"
            style={{ top: "50%" }}
          />
        )}
      </div>
      <span className="text-[10px] font-bold tracking-[0.2em]">
        {label}
      </span>
    </div>
  );
}
