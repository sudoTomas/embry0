import type { Node, Edge } from "@xyflow/react";
import { Link } from "react-router";
import { useAgents } from "@/hooks/useAgents";
import { useIdentityLookup } from "@/hooks/useAgentIdentity";
import { AgentAvatar } from "@/components/agents/AgentAvatar";
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

export function AgentBar({ nodes, edges }: AgentBarProps) {
  const { data: agents } = useAgents();
  const lookup = useIdentityLookup();

  // Every agent_definitions row a job can run except triage (the root the
  // editor places itself: it runs in every job, so it is no runs_in_jobs
  // row), plus the terminal `output` sentinel, valid template vocabulary with
  // no agent row behind it (it runs the finalize_output code node). The
  // backend validator refuses a row no job runs (runs_in_jobs false).
  const available = [
    ...new Set([
      ...(agents ?? []).filter((a) => a.type !== "triage" && a.runs_in_jobs !== false).map((a) => a.type),
      "output",
    ]),
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
    <div className="flex items-center gap-3 px-5 py-2.5 border-t border-white/[0.06] bg-card overflow-x-auto shrink-0">
      <span className="text-[10px] uppercase tracking-[0.08em] text-white/25 font-semibold whitespace-nowrap mr-1">
        Agents
      </span>

      {available.map((agentType) => {
        const identity = lookup(agentType);
        return (
          <div
            key={agentType}
            draggable
            onDragStart={(e) => onDragStart(e, agentType)}
            title={identity.tagline || agentType}
            className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg border text-xs font-medium whitespace-nowrap cursor-grab hover:-translate-y-0.5 transition-all"
            style={{
              background: `${identity.hex}14`,
              borderColor: `${identity.hex}40`,
              color: `${identity.hex}E6`,
            }}
          >
            <AgentAvatar identity={identity} size={16} />
            {identity.displayName}
          </div>
        );
      })}

      {/* Separator */}
      <div className="w-px h-5 bg-white/[0.08] shrink-0" />

      {/* New agent: the form, not a placeholder node */}
      <Link
        to="/agents/new"
        className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-dashed border-white/15 text-xs font-medium whitespace-nowrap hover:-translate-y-0.5 transition-all text-white/50 bg-white/[0.03]"
      >
        + New agent
      </Link>

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
