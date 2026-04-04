import type { Node, Edge } from "@xyflow/react";
import type { AgentNode, PipelineEdge, PipelineGraph, PipelineMetadata, EdgeType } from "./types";

// Canonical color map for React Flow nodes. Tailwind class equivalents are in constants.ts (ROLE_COLORS).
// Agent type -> color category
const AGENT_COLORS: Record<string, string> = {
  explorer: "#06b6d4", // cyan
  "frontend-explorer": "#06b6d4",
  developer: "#f59e0b", // amber
  "code-gen": "#f59e0b",
  "docs-writer": "#f59e0b",
  "test-runner": "#22c55e", // green
  "lint-checker": "#22c55e",
  "type-checker": "#22c55e",
  "visual-validator": "#22c55e",
  reviewer: "#a855f7", // purple
  "security-reviewer": "#a855f7",
  triage: "#a855f7",
  validator: "#22c55e", // green
  output: "#f43f5e", // rose
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
 * Convert React Flow state to PipelineGraph API format.
 */
export function serializeGraph(
  rfNodes: Node[],
  rfEdges: Edge[],
  metadata: PipelineMetadata,
  name: string = "Custom Pipeline",
  graphId?: string,
): PipelineGraph {
  const nodes: AgentNode[] = rfNodes.map((n) => ({
    node_id: n.id,
    agent_type: (n.data as Record<string, unknown>).agentType as string,
    label: ((n.data as Record<string, unknown>).label as string) ?? "",
    position: { x: n.position.x, y: n.position.y },
    model: ((n.data as Record<string, unknown>).model as string | undefined) ?? null,
    max_budget_usd: ((n.data as Record<string, unknown>).maxBudgetUsd as number | undefined) ?? null,
    max_turns: ((n.data as Record<string, unknown>).maxTurns as number | undefined) ?? null,
    effort: ((n.data as Record<string, unknown>).effort as string | undefined) ?? null,
    tools: ((n.data as Record<string, unknown>).tools as string[] | undefined) ?? null,
    skills: ((n.data as Record<string, unknown>).skills as string[] | undefined) ?? null,
    prompt_prepend: ((n.data as Record<string, unknown>).promptPrepend as string | undefined) ?? null,
    prompt_append: ((n.data as Record<string, unknown>).promptAppend as string | undefined) ?? null,
    custom_prompt: ((n.data as Record<string, unknown>).customPrompt as string | undefined) ?? null,
    custom_tools: ((n.data as Record<string, unknown>).customTools as string[] | undefined) ?? null,
    sandbox: ((n.data as Record<string, unknown>).sandbox as AgentNode["sandbox"]) ?? null,
  }));

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
 * Convert PipelineGraph API format to React Flow state.
 */
export function deserializeGraph(graph: PipelineGraph): { nodes: Node[]; edges: Edge[] } {
  const nodes: Node[] = graph.nodes.map((n) => ({
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
  }));

  const edges: Edge[] = graph.edges.map((e) => ({
    id: e.edge_id,
    source: e.source,
    target: e.target,
    type: e.edge_type === "feedback" ? "feedbackEdge" : "default",
    animated: e.edge_type === "feedback",
    style: e.edge_type === "feedback" ? { stroke: "#f87171", strokeDasharray: "6 4" } : undefined,
    data: {
      edgeType: e.edge_type,
      loopConfig: e.loop_config,
    },
  }));

  return { nodes, edges };
}
