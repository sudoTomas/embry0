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
import { FeedbackEdge } from "./FeedbackEdge";
import { AgentPalette } from "./AgentPalette";
import { EmptyCanvas } from "./EmptyCanvas";
import { NodeInspector } from "./NodeInspector";
import { EdgeInspector } from "./EdgeInspector";
import { ValidationBar } from "./ValidationBar";
import { TemplatePicker } from "./TemplatePicker";
import { TemplateDrawer } from "./TemplateDrawer";
import { useGraphState } from "./hooks/useGraphState";
import { useRenameTemplate, useCreateTemplate } from "@/hooks/usePipelines";
import type { PipelineGraph } from "@/lib/types";

const nodeTypes = { agentNode: AgentNode };
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
  onPaneClick: () => void;
}

/** Inner component rendered inside ReactFlowProvider -- can safely call useReactFlow(). */
function PipelineCanvas({ graphState, onNodeClick, onEdgeClick, onPaneClick }: PipelineCanvasProps) {
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
    <div className="flex-1 min-h-0 relative">
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
        onPaneClick={onPaneClick}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        proOptions={{ hideAttribution: true }}
        className="bg-[#0a0c14]"
      >
        <Background color="rgba(255,255,255,0.03)" gap={24} />
        <MiniMap className="!bg-[#111318] !border-white/[0.06]" nodeColor="#ffffff20" />
        <Controls className="!bg-[#111318] !border-white/[0.06] !shadow-lg" />
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
      <div className="flex flex-1 min-h-0 relative">
        {/* Template drawer (page mode) overlays the agent palette area */}
        {!isModal && showTemplateDrawer && (
          <TemplateDrawer
            onSelect={(graph) => {
              handleTemplateSelect(graph);
              setShowTemplateDrawer(false);
            }}
            onClose={() => setShowTemplateDrawer(false)}
          />
        )}
        <AgentPalette />
        <div className="flex-1 flex flex-col min-h-0">
          <ReactFlowProvider>
            <PipelineCanvas
              graphState={graphState}
              onNodeClick={(nodeId) => {
                setSelectedNode(nodeId);
                setSelectedEdge(null);
              }}
              onEdgeClick={(edgeId) => {
                setSelectedEdge(edgeId);
                setSelectedNode(null);
              }}
              onPaneClick={() => {
                setSelectedNode(null);
                setSelectedEdge(null);
              }}
            />
          </ReactFlowProvider>
          {isModal && <ValidationBar nodes={nodes} edges={edges} onApply={handleApply} />}
          {!isModal && <ValidationBar nodes={nodes} edges={edges} onApply={handleSaveAs} />}
        </div>
        {/* Inspector panel */}
        <div className="w-[280px] shrink-0 bg-[#0f1117] border-l border-white/[0.08] overflow-y-auto">
          {selectedNode && (
            <NodeInspector
              node={nodes.find((n) => n.id === selectedNode)!}
              onUpdate={(data) => updateNodeData(selectedNode, data)}
            />
          )}
          {selectedEdge && (
            <EdgeInspector
              edge={edges.find((e) => e.id === selectedEdge)!}
              onUpdate={(data) => updateEdgeData(selectedEdge, data)}
            />
          )}
          {!selectedNode && !selectedEdge && (
            <div className="flex flex-col items-center justify-center h-full text-center px-6">
              <p className="text-white/25 text-sm">Select a node or edge to configure</p>
            </div>
          )}
        </div>
      </div>

      {isModal && showTemplatePicker && (
        <TemplatePicker onSelect={loadGraph} onClose={() => setShowTemplatePicker(false)} />
      )}
    </div>
  );
}
