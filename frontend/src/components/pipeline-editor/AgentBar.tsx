import type { Node, Edge } from "@xyflow/react";
import { useAgents } from "@/hooks/useAgents";
import { getAgentColor } from "@/lib/graph-utils";
import { cn } from "@/lib/utils";

function hasPathStartToEnd(nodes: Node[], edges: Edge[]): boolean {
  const startIds = nodes
    .filter((n) => (n.data as Record<string, unknown>).nodeRole === "start")
    .map((n) => n.id);
  const endIds = new Set(
    nodes
      .filter((n) => (n.data as Record<string, unknown>).nodeRole === "end")
      .map((n) => n.id),
  );

  if (startIds.length === 0 || endIds.size === 0) return false;

  const adjacency = new Map<string, string[]>();
  for (const edge of edges) {
    if (!adjacency.has(edge.source)) adjacency.set(edge.source, []);
    adjacency.get(edge.source)!.push(edge.target);
  }

  const visited = new Set<string>();
  const queue = [...startIds];
  while (queue.length > 0) {
    const current = queue.shift()!;
    if (endIds.has(current)) return true;
    if (visited.has(current)) continue;
    visited.add(current);
    for (const neighbor of adjacency.get(current) ?? []) {
      queue.push(neighbor);
    }
  }
  return false;
}

interface AgentBarProps {
  nodes: Node[];
  edges: Edge[];
}

// The template vocabulary the backend can save + execute (mirror of
// workflows/_validation.py _EXECUTABLE_AGENT_TYPES, minus triage — an
// orchestrator concern, not a pipeline node). RAV-1288: the palette used
// to offer every agent_definitions row, which included placeable-but-
// unsaveable rows (validator, onboarding) and, before the zombie cleanup,
// relied on the resurrected `reviewer` row for review steps.
const TEMPLATE_AGENT_TYPES = new Set([
  "developer",
  "reviewer",
  "review",
  "qa",
  "research",
  "analysis",
  "ops",
]);

export function AgentBar({ nodes, edges }: AgentBarProps) {
  const { data: agents } = useAgents();

  // Agent-backed chips from the registry, plus the terminal `output` step —
  // valid template vocabulary with no agent row behind it (it runs the
  // finalize_output code node, not an agent).
  const available = [
    ...(agents ?? []).filter((a) => TEMPLATE_AGENT_TYPES.has(a.type)).map((a) => a.type),
    "output",
  ];

  const feedbackCount = edges.filter(
    (e) => (e.data as Record<string, unknown> | undefined)?.edgeType === "feedback",
  ).length;

  const isValid = hasPathStartToEnd(nodes, edges);

  const onDragStart = (event: React.DragEvent, agentType: string) => {
    event.dataTransfer.setData("application/agentType", agentType);
    event.dataTransfer.effectAllowed = "move";
  };

  return (
    <div className="flex items-center gap-3 px-5 py-2.5 border-t border-white/[0.06] bg-surface overflow-x-auto shrink-0">
      <span className="text-[10px] uppercase tracking-[0.08em] text-white/25 font-semibold whitespace-nowrap mr-1">
        Agents
      </span>

      {available.map((agentType) => {
        const color = getAgentColor(agentType);
        return (
          <div
            key={agentType}
            draggable
            onDragStart={(e) => onDragStart(e, agentType)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border text-xs font-medium whitespace-nowrap cursor-grab hover:-translate-y-0.5 transition-all"
            style={{
              background: `${color}14`,
              borderColor: `${color}40`,
              color: `${color}E6`,
            }}
          >
            <span
              className="w-1.5 h-1.5 rounded-full shrink-0"
              style={{ background: color }}
            />
            {agentType}
          </div>
        );
      })}

      {/* Separator */}
      <div className="w-px h-5 bg-white/[0.08] shrink-0" />

      {/* Custom agent chip */}
      <div
        draggable
        onDragStart={(e) => onDragStart(e, "custom")}
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-dashed border-white/15 text-xs font-medium whitespace-nowrap cursor-grab hover:-translate-y-0.5 transition-all text-white/50 bg-white/[0.03]"
      >
        + Custom Agent
      </div>

      {/* Validation stats (right-aligned) */}
      <div className="ml-auto flex items-center gap-3 text-[11px] text-white/35 whitespace-nowrap">
        <span>{nodes.length} node{nodes.length !== 1 ? "s" : ""}</span>
        <span>{edges.length} edge{edges.length !== 1 ? "s" : ""}</span>
        {feedbackCount > 0 && (
          <span className="text-destructive/60">
            {feedbackCount} loop{feedbackCount !== 1 ? "s" : ""}
          </span>
        )}
        <span
          className={cn(
            "px-2 py-0.5 rounded-full text-[10px] font-medium",
            isValid
              ? "bg-success/10 text-success/80"
              : "bg-amber-500/10 text-amber-400/80",
          )}
        >
          {isValid ? "Valid" : "Connect Start \u2192 End"}
        </span>
      </div>
    </div>
  );
}
