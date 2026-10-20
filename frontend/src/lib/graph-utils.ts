import type { Node, Edge } from "@xyflow/react";
import type { AgentNode, PipelineEdge, PipelineGraph, PipelineMetadata, EdgeType } from "./types";

// Canonical color map for React Flow nodes. Tailwind class equivalents are in constants.ts (ROLE_COLORS).
// Agent type -> color category
const AGENT_COLORS: Record<string, string> = {
  explorer: "#3b82f6", // blue
  "frontend-explorer": "#3b82f6",
  developer: "#f59e0b", // amber
  "code-gen": "#f59e0b",
  "docs-writer": "#f59e0b",
  "test-runner": "#10b981", // teal
  "lint-checker": "#10b981",
  "type-checker": "#10b981",
  "visual-validator": "#10b981",
  reviewer: "#a78bfa", // purple
  "security-reviewer": "#a78bfa",
  triage: "#a78bfa",
  validator: "#10b981", // teal
  output: "#f97316", // orange
  custom: "#6b7280", // gray
};

export function getAgentColor(agentType: string): string {
  return AGENT_COLORS[agentType] ?? "#6b7280";
}

export function getAgentCategory(agentType: string): string {
  if (["explorer", "frontend-explorer"].includes(agentType)) return "Exploration";
  if (["developer", "code-gen", "docs-writer"].includes(agentType)) return "Development";
  if (["test-runner", "lint-checker", "type-checker", "visual-validator", "validator"].includes(agentType)) return "Validation";
  if (["reviewer", "security-reviewer"].includes(agentType)) return "Review";
  if (agentType === "triage") return "Triage";
  if (agentType === "output") return "Output";
  return "Custom";
}

/**
 * DFS-based cycle detection. Returns set of edge IDs that are back-edges.
 */
export function detectCycles(
  nodes: { id: string }[],
  edges: { id: string; source: string; target: string }[],
): Set<string> {
  const backEdges = new Set<string>();
  const WHITE = 0,
    GRAY = 1,
    BLACK = 2;
  const color: Record<string, number> = {};
  const edgeMap = new Map<string, { id: string; target: string }[]>();

  for (const n of nodes) color[n.id] = WHITE;
  for (const e of edges) {
    if (!edgeMap.has(e.source)) edgeMap.set(e.source, []);
    edgeMap.get(e.source)!.push({ id: e.id, target: e.target });
  }

  function dfs(nodeId: string) {
    color[nodeId] = GRAY;
    for (const edge of edgeMap.get(nodeId) ?? []) {
      if (color[edge.target] === GRAY) {
        backEdges.add(edge.id);
      } else if (color[edge.target] === WHITE) {
        dfs(edge.target);
      }
    }
    color[nodeId] = BLACK;
  }

  for (const n of nodes) {
    if (color[n.id] === WHITE) dfs(n.id);
  }
  return backEdges;
}

/**
 * Whether a React Flow node is the canvas's START or END, and which. The
 * canvas types both "startEndNode" and tells them apart by data.nodeRole, so
 * a check on node.type finds neither; the layouts ask here.
 */
export function startEndRole(n: Node): "start" | "end" | null {
  const role = (n.data as Record<string, unknown> | undefined)?.nodeRole;
  return role === "start" || role === "end" ? role : null;
}

/**
 * Helper: check if a React Flow node is a Start/End structural node.
 */
function isStartEndNode(n: Node): boolean {
  return startEndRole(n) !== null;
}

/**
 * Convert React Flow state to PipelineGraph API format.
 * Start/End nodes are serialized with special agent_type "__start__" / "__end__"
 * so the backend can reconstruct the flow topology.
 */
export function serializeGraph(
  rfNodes: Node[],
  rfEdges: Edge[],
  metadata: PipelineMetadata,
  name: string = "Custom Pipeline",
  graphId?: string,
): PipelineGraph {
  const nodes: AgentNode[] = rfNodes.map((n) => {
    const d = n.data as Record<string, unknown>;
    if (isStartEndNode(n)) {
      const role = d.nodeRole as "start" | "end";
      return {
        node_id: n.id,
        agent_type: role === "start" ? "__start__" : "__end__",
        label: role === "start" ? "START" : "END",
        position: { x: n.position.x, y: n.position.y },
        model: null,
        max_budget_usd: null,
        max_turns: null,
        effort: null,
        tools: null,
        skills: null,
        prompt_prepend: null,
        prompt_append: null,
        custom_prompt: null,
        custom_tools: null,
        sandbox: null,
      };
    }
    return {
      node_id: n.id,
      agent_type: d.agentType as string,
      label: (d.label as string) ?? "",
      position: { x: n.position.x, y: n.position.y },
      model: (d.model as string | undefined) ?? null,
      max_budget_usd: (d.maxBudgetUsd as number | undefined) ?? null,
      max_turns: (d.maxTurns as number | undefined) ?? null,
      effort: (d.effort as string | undefined) ?? null,
      tools: (d.tools as string[] | undefined) ?? null,
      skills: (d.skills as string[] | undefined) ?? null,
      prompt_prepend: (d.promptPrepend as string | undefined) ?? null,
      prompt_append: (d.promptAppend as string | undefined) ?? null,
      custom_prompt: (d.customPrompt as string | undefined) ?? null,
      custom_tools: (d.customTools as string[] | undefined) ?? null,
      sandbox: (d.sandbox as AgentNode["sandbox"]) ?? null,
    };
  });

  const edges: PipelineEdge[] = rfEdges.map((e) => ({
    edge_id: e.id,
    source: e.source,
    target: e.target,
    edge_type: (((e.data as Record<string, unknown>)?.edgeType as EdgeType) ?? "flow"),
    loop_config: ((e.data as Record<string, unknown>)?.loopConfig as PipelineEdge["loop_config"]) ?? null,
  }));

  return {
    graph_id: graphId ?? `graph-${Date.now()}`,
    name,
    nodes,
    edges,
    metadata,
  };
}

/**
 * Strip the canvas's START/END scaffolding for the API.
 *
 * START and END exist to make the flow readable on the canvas; they are not
 * agents. The backend validator only knows executable agent types and rejects
 * `__start__`/`__end__` outright ("unknown agent_type"), which made every save
 * from the editor fail (RAV-1293). Stored templates carry agent nodes and the
 * flow edges between them and nothing else, so drop the scaffolding nodes plus
 * any edge touching one. The load path re-adds them via `injectStartEndNodes`,
 * so the round-trip stays symmetric.
 */
export function toApiGraph(graph: PipelineGraph): PipelineGraph {
  const scaffoldIds = new Set(
    graph.nodes.filter((n) => n.agent_type === "__start__" || n.agent_type === "__end__").map((n) => n.node_id),
  );
  if (scaffoldIds.size === 0) return graph;
  return {
    ...graph,
    nodes: graph.nodes.filter((n) => !scaffoldIds.has(n.node_id)),
    edges: graph.edges.filter((e) => !scaffoldIds.has(e.source) && !scaffoldIds.has(e.target)),
  };
}

/**
 * Convert PipelineGraph API format to React Flow state.
 */
export function deserializeGraph(graph: PipelineGraph): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = graph.nodes.map((n) => {
    if (n.agent_type === "__start__" || n.agent_type === "__end__") {
      const role = n.agent_type === "__start__" ? "start" : "end";
      return {
        id: n.node_id,
        type: "startEndNode",
        position: { x: n.position.x, y: n.position.y },
        deletable: false,
        data: { nodeRole: role },
      };
    }
    return {
      id: n.node_id,
      type: "agentNode",
      position: { x: n.position.x, y: n.position.y },
      data: {
        agentType: n.agent_type,
        label: n.label || n.agent_type,
        model: n.model,
        maxBudgetUsd: n.max_budget_usd,
        maxTurns: n.max_turns,
        effort: n.effort,
        tools: n.tools,
        skills: n.skills,
        promptPrepend: n.prompt_prepend,
        promptAppend: n.prompt_append,
        customPrompt: n.custom_prompt,
        customTools: n.custom_tools,
        sandbox: n.sandbox,
      },
    };
  });

  const edges: Edge[] = graph.edges.map((e) => ({
    id: e.edge_id,
    source: e.source,
    target: e.target,
    type: e.edge_type === "feedback" ? "feedbackEdge" : "default",
    animated: e.edge_type === "feedback",
    data: {
      edgeType: e.edge_type,
      loopConfig: e.loop_config,
    },
  }));

  return { nodes, edges };
}

/**
 * Ensure Start and End nodes exist in the React Flow graph.
 * If missing, inject them at sensible positions relative to existing nodes,
 * connected to the graph's roots and leaves.
 */
export function injectStartEndNodes(graph: { nodes: Node[]; edges: Edge[] }): { nodes: Node[]; edges: Edge[] } {
  const { nodes, edges } = graph;
  const hasStart = nodes.some((n) => (n.data as Record<string, unknown>).nodeRole === "start");
  const hasEnd = nodes.some((n) => (n.data as Record<string, unknown>).nodeRole === "end");

  if (hasStart && hasEnd) return graph;

  const agentNodes = nodes.filter(
    (n) => (n.data as Record<string, unknown>).nodeRole !== "start" && (n.data as Record<string, unknown>).nodeRole !== "end",
  );

  const injected: Node[] = [...nodes];

  if (!hasStart) {
    const leftmost = agentNodes.length > 0 ? Math.min(...agentNodes.map((n) => n.position.x)) : 300;
    injected.push({
      id: "__start__",
      type: "startEndNode",
      position: { x: agentNodes.length > 0 ? leftmost - 200 : 100, y: 250 },
      deletable: false,
      data: { nodeRole: "start" },
    });
  }

  if (!hasEnd) {
    const rightmost = agentNodes.length > 0 ? Math.max(...agentNodes.map((n) => n.position.x)) : 100;
    injected.push({
      id: "__end__",
      type: "startEndNode",
      position: { x: agentNodes.length > 0 ? rightmost + 300 : 500, y: 250 },
      deletable: false,
      data: { nodeRole: "end" },
    });
  }

  // A stored graph has no scaffold edges (toApiGraph strips them), so connect
  // what was injected: Start to every agent no flow edge enters, every agent
  // no flow edge leaves to End. Feedback edges loop back and do not count.
  const flow = edges.filter((e) => (e.data as Record<string, unknown> | undefined)?.edgeType !== "feedback");
  const hasIncoming = new Set(flow.map((e) => e.target));
  const hasOutgoing = new Set(flow.map((e) => e.source));
  const scaffold = (source: string, target: string): Edge => ({
    id: `${source}->${target}`,
    source,
    target,
    type: "default",
    data: { edgeType: "flow" },
  });
  const added: Edge[] = [];
  if (!hasStart) {
    for (const n of agentNodes) if (!hasIncoming.has(n.id)) added.push(scaffold("__start__", n.id));
  }
  if (!hasEnd) {
    for (const n of agentNodes) if (!hasOutgoing.has(n.id)) added.push(scaffold(n.id, "__end__"));
  }

  return { nodes: injected, edges: [...edges, ...added] };
}
