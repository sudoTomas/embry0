import { useState, useCallback } from "react";
import {
  ReactFlowProvider,
  ReactFlow,
  MiniMap,
  Controls,
  Background,
  useReactFlow,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { X, Save, FolderOpen } from "lucide-react";
import { AgentNode } from "./AgentNode";
import { StartEndNode } from "./StartEndNode";
import { FeedbackEdge } from "./FeedbackEdge";
import { AgentBar } from "./AgentBar";
import { EmptyCanvas } from "./EmptyCanvas";
import { AgentDetailPopup } from "./AgentDetailPopup";
import { EdgeDetailPopup } from "./EdgeDetailPopup";
import { TemplatePicker } from "./TemplatePicker";
import { TemplateDrawer } from "./TemplateDrawer";
import { useGraphState } from "./hooks/useGraphState";
import { useRenameTemplate, useCreateTemplate } from "@/hooks/usePipelines";
import type { PipelineGraph } from "@/lib/types";

const nodeTypes = { agentNode: AgentNode, startEndNode: StartEndNode };
const edgeTypes = { feedbackEdge: FeedbackEdge };

interface PipelineEditorProps {
  mode?: "modal" | "page";
  initialGraph?: PipelineGraph;
  onApply?: (graph: PipelineGraph) => void;
  onClose?: () => void;
}

interface PipelineCanvasProps {
  graphState: ReturnType<typeof useGraphState>;
  onNodeClick: (nodeId: string) => void;
  onEdgeClick: (edgeId: string) => void;
}

/** Inner component rendered inside ReactFlowProvider -- can safely call useReactFlow(). */
function PipelineCanvas({ graphState, onNodeClick, onEdgeClick }: PipelineCanvasProps) {
  const { screenToFlowPosition } = useReactFlow();
  const { nodes, edges, onNodesChange, onEdgesChange, onConnect, onDragOver, setNodes } = graphState;

  const onDrop = useCallback(
    (event: React.DragEvent) => {
      event.preventDefault();
      const agentType = event.dataTransfer.getData("application/agentType");
      if (!agentType) return;

      const position = screenToFlowPosition({ x: event.clientX, y: event.clientY });
      const newNode: Node = {
        id: `node-${Date.now()}`,
        type: "agentNode",
        position,
        data: { agentType, label: agentType },
      };
      setNodes((nds) => [...nds, newNode]);
    },
    [screenToFlowPosition, setNodes],
  );

  return (
    <div className="flex-1 min-h-0 relative" style={{ background: 'radial-gradient(circle at 50% 50%, rgba(6,182,212,0.015) 0%, transparent 60%)' }}>
      {nodes.length === 0 && <EmptyCanvas />}
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onDrop={onDrop}
        onDragOver={onDragOver}
        onNodeClick={(_, node) => onNodeClick(node.id)}
        onEdgeClick={(_, edge) => onEdgeClick(edge.id)}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        proOptions={{ hideAttribution: true }}
        className="bg-[#0c0e12]"
      >
        <Background color="rgba(255,255,255,0.018)" gap={24} />
        <MiniMap className="!bg-[rgba(15,20,25,0.9)] !border-white/[0.06]" nodeColor="#ffffff20" />
        <Controls className="!bg-[rgba(15,20,25,0.9)] !border-white/[0.06] !shadow-lg" />
      </ReactFlow>
    </div>
  );
}

export function PipelineEditor({ mode = "modal", initialGraph, onApply, onClose }: PipelineEditorProps) {
  const graphState = useGraphState(initialGraph);
  const {
    nodes,
    edges,
    selectedNode,
    selectedEdge,
    setSelectedNode,
    setSelectedEdge,
    serialize,
    loadGraph,
    updateNodeData,
    updateEdgeData,
  } = graphState;

  const [showTemplatePicker, setShowTemplatePicker] = useState(false);
  const [showTemplateDrawer, setShowTemplateDrawer] = useState(false);

  // Page-mode state
  const [currentTemplateId, setCurrentTemplateId] = useState<string | null>(null);
  const [pipelineName, setPipelineName] = useState("Untitled pipeline");

  const renameMutation = useRenameTemplate();
  const createMutation = useCreateTemplate();

  const handleApply = useCallback(() => {
    onApply?.(serialize());
  }, [onApply, serialize]);

  const handleTemplateSelect = useCallback(
    (graph: PipelineGraph) => {
      loadGraph(graph);
      if (mode === "page") {
        setPipelineName(graph.name || "Untitled pipeline");
        setCurrentTemplateId(graph.metadata?.template_id ?? null);
      }
    },
    [loadGraph, mode],
  );

  const handleSave = useCallback(() => {
    if (!currentTemplateId) return;
    const graph = serialize(pipelineName);
    renameMutation.mutate({
      templateId: currentTemplateId,
      name: pipelineName,
      graph: graph as unknown as Record<string, unknown>,
    });
  }, [currentTemplateId, pipelineName, serialize, renameMutation]);

  const handleSaveAs = useCallback(() => {
    const name = window.prompt("Template name:", pipelineName);
    if (!name?.trim()) return;
    const graph = serialize(name);
    createMutation.mutate(
      { name, description: "", graph },
      {
        onSuccess: (created) => {
          setCurrentTemplateId(created.template_id);
          setPipelineName(name);
        },
      },
    );
  }, [pipelineName, serialize, createMutation]);

  const isModal = mode === "modal";

  const header = isModal ? (
    /* Modal header */
    <div className="h-14 shrink-0 border-b border-white/[0.08] flex items-center justify-between px-4 bg-[#0f1117]">
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onClose}
          className="p-1.5 rounded-md hover:bg-white/[0.06] text-white/50 hover:text-white/80 transition-colors"
        >
          <X size={18} />
        </button>
        <h2 className="text-base font-semibold text-white">Pipeline Editor</h2>
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setShowTemplatePicker(true)}
          className="text-xs text-white/50 hover:text-white/70 px-3 py-1.5 rounded-md hover:bg-white/[0.04] transition-colors"
        >
          Load Template
        </button>
        <button
          type="button"
          onClick={handleApply}
          disabled={nodes.length === 0}
          className="bg-blue-600 hover:bg-blue-500 disabled:opacity-30 text-white text-xs font-medium px-4 py-1.5 rounded-md transition-colors"
        >
          Apply Pipeline
        </button>
      </div>
    </div>
  ) : (
    /* Page header */
    <div className="h-14 shrink-0 border-b border-white/[0.08] flex items-center justify-between px-4 bg-[#0f1117]">
      <div className="flex items-center gap-3">
        <input
          type="text"
          value={pipelineName}
          onChange={(e) => setPipelineName(e.target.value)}
          className="bg-transparent border-none text-base font-semibold text-white outline-none focus:ring-1 focus:ring-white/20 rounded px-1.5 py-0.5 -ml-1.5 w-64"
          placeholder="Pipeline name..."
        />
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setShowTemplateDrawer(true)}
          className="flex items-center gap-1.5 text-xs text-white/50 hover:text-white/70 px-3 py-1.5 rounded-md hover:bg-white/[0.04] transition-colors"
        >
          <FolderOpen size={13} />
          Templates
        </button>
        <button
          type="button"
          onClick={handleSave}
          disabled={!currentTemplateId || nodes.length === 0 || renameMutation.isPending}
          className="flex items-center gap-1.5 bg-white/[0.06] hover:bg-white/[0.1] disabled:opacity-30 text-white/70 text-xs font-medium px-3 py-1.5 rounded-md transition-colors"
        >
          <Save size={13} />
          Save
        </button>
        <button
          type="button"
          onClick={handleSaveAs}
          disabled={nodes.length === 0 || createMutation.isPending}
          className="bg-blue-600 hover:bg-blue-500 disabled:opacity-30 text-white text-xs font-medium px-4 py-1.5 rounded-md transition-colors"
        >
          Save As
        </button>
      </div>
    </div>
  );

  return (
    <div className={isModal ? "fixed inset-0 z-50 bg-[#09090b] flex flex-col" : "flex flex-col h-full bg-[#09090b]"}>
      {header}

      {/* Main content */}
      <div className="flex-1 flex flex-col min-h-0 relative">
        {/* Template drawer (page mode) overlays from left */}
        {!isModal && showTemplateDrawer && (
          <TemplateDrawer
            onSelect={(graph) => {
              handleTemplateSelect(graph);
              setShowTemplateDrawer(false);
            }}
            onClose={() => setShowTemplateDrawer(false)}
          />
        )}

        {/* Full-width canvas */}
        <ReactFlowProvider>
          <PipelineCanvas
            graphState={graphState}
            onNodeClick={(nodeId) => {
              const node = nodes.find((n) => n.id === nodeId);
              if (node?.data?.nodeRole === "start" || node?.data?.nodeRole === "end") return;
              setSelectedNode(nodeId);
              setSelectedEdge(null);
            }}
            onEdgeClick={(edgeId) => {
              setSelectedEdge(edgeId);
              setSelectedNode(null);
            }}
          />
        </ReactFlowProvider>

        {/* Overlay popups */}
        {selectedNode && nodes.find((n) => n.id === selectedNode) && (
          <AgentDetailPopup
            node={nodes.find((n) => n.id === selectedNode)!}
            onUpdate={(data) => updateNodeData(selectedNode, data)}
            onClose={() => setSelectedNode(null)}
          />
        )}
        {selectedEdge && edges.find((e) => e.id === selectedEdge) && (
          <EdgeDetailPopup
            edge={edges.find((e) => e.id === selectedEdge)!}
            onUpdate={(data) => updateEdgeData(selectedEdge, data)}
            onClose={() => setSelectedEdge(null)}
          />
        )}
      </div>

      {/* Bottom agent bar (outside the flex-1 canvas area) */}
      <AgentBar nodes={nodes} edges={edges} />

      {isModal && showTemplatePicker && (
        <TemplatePicker onSelect={loadGraph} onClose={() => setShowTemplatePicker(false)} />
      )}
    </div>
  );
}
