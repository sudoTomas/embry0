import { useState, useCallback, useId } from "react";
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
import { X, Save, FolderOpen, LayoutGrid } from "lucide-react";
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
import { autoLayout } from "./autoLayout";
import { DivineRipple } from "@/components/divine/DivineRipple";
import { Button } from "@/components/ui/Button";
import { Overlay } from "@/components/ui/Overlay";
import { useRenameTemplate, useCreateTemplate } from "@/hooks/usePipelines";
import { toApiGraph } from "@/lib/graph-utils";
import type { PipelineGraph, PipelineTemplate } from "@/lib/types";
import { toastApiError } from "@/api/errorToast";

const nodeTypes = { agentNode: AgentNode, startEndNode: StartEndNode };
const edgeTypes = { feedbackEdge: FeedbackEdge };

/**
 * How far the canvas zooms out, by hand or in its first fit. React Flow's
 * default of 0.5 stopped the fit short: a seven-agent pipeline lays out
 * about 1500 px wide, so on a phone, or any canvas under about 840 px,
 * START and END sat off its sides. At 0.1 it fits a 360 px screen.
 */
const MIN_ZOOM = 0.1;

// embry0 re-seeds every built-in template on each restart, over any edit.
const BUILTIN_HINT = "Built-in templates reset on restart. Save As makes your own copy.";

interface PipelineEditorProps {
  mode?: "modal" | "page";
  initialGraph?: PipelineGraph;
  /** The saved template the page opened, so Save writes back to it. */
  initialTemplate?: Pick<PipelineTemplate, "template_id" | "name" | "is_builtin">;
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
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        fitView
        minZoom={MIN_ZOOM}
        proOptions={{ hideAttribution: true }}
        className="bg-page"
      >
        <Background color="rgba(255,255,255,0.018)" gap={24} />
        <MiniMap
          className="!bg-[rgba(15,20,25,0.9)] !border-white/[0.06] !w-[120px] !h-[75px]"
          nodeColor="#ffffff20"
          // React Flow's default mask is a light grey that reads as a block on
          // the dark canvas; the page colour at 70% keeps the viewport window.
          maskColor="rgb(10 10 15 / 0.7)"
        />
        <Controls className="!bg-[rgba(15,20,25,0.9)] !border-white/[0.06] !shadow-lg" />
      </ReactFlow>
    </div>
  );
}

export function PipelineEditor({ mode = "modal", initialGraph, initialTemplate, onApply, onClose }: PipelineEditorProps) {
  const graphState = useGraphState(initialGraph);
  const {
    nodes,
    edges,
    selectedNode,
    selectedEdge,
    setSelectedNode,
    setSelectedEdge,
    setNodes,
    setEdges,
    serialize,
    loadGraph,
    updateNodeData,
    updateEdgeData,
  } = graphState;

  const [showTemplatePicker, setShowTemplatePicker] = useState(false);
  const [showTemplateDrawer, setShowTemplateDrawer] = useState(false);
  const [rippleKey, setRippleKey] = useState(0);

  // Page-mode state
  const [currentTemplateId, setCurrentTemplateId] = useState<string | null>(initialTemplate?.template_id ?? null);
  // A built-in can be opened and edited, but only Save As keeps the edit.
  const [isBuiltin, setIsBuiltin] = useState(initialTemplate?.is_builtin === true);
  const canSaveInPlace = currentTemplateId !== null && !isBuiltin;
  const builtinHintId = useId();
  const [pipelineName, setPipelineName] = useState(
    initialTemplate?.name || initialGraph?.name || "Untitled pipeline",
  );

  const renameMutation = useRenameTemplate();
  const createMutation = useCreateTemplate();

  // Everything leaving the editor goes through toApiGraph: the canvas keeps
  // START/END for readability, the API only accepts real agent nodes.
  const handleApply = useCallback(() => {
    onApply?.(toApiGraph(serialize()));
  }, [onApply, serialize]);

  const handleTemplateSelect = useCallback(
    (template: PipelineTemplate) => {
      loadGraph(template.graph);
      if (mode === "page") {
        setPipelineName(template.name || template.graph.name || "Untitled pipeline");
        // The id lives on the template, not inside its graph — reading
        // graph.metadata here left Save disabled forever (RAV-1293).
        setCurrentTemplateId(template.template_id ?? null);
        setIsBuiltin(template.is_builtin === true);
      }
    },
    [loadGraph, mode],
  );

  const handleSave = useCallback(() => {
    if (!currentTemplateId || isBuiltin) return;
    const graph = toApiGraph(serialize(pipelineName));
    renameMutation.mutate(
      { templateId: currentTemplateId, name: pipelineName, graph },
      { onError: (e) => toastApiError(e, "Failed to save the pipeline") },
    );
  }, [currentTemplateId, isBuiltin, pipelineName, serialize, renameMutation]);

  const handleAutoArrange = useCallback(() => {
    const result = autoLayout(nodes, edges);
    setNodes(result.nodes);
    setEdges(result.edges);
    setRippleKey((k) => k + 1);
  }, [nodes, edges, setNodes, setEdges]);

  const handleSaveAs = useCallback(() => {
    const name = window.prompt("Template name:", pipelineName);
    if (!name?.trim()) return;
    const graph = toApiGraph(serialize(name));
    createMutation.mutate(
      { name, description: "", graph },
      {
        onSuccess: (created) => {
          setCurrentTemplateId(created.template_id);
          setIsBuiltin(false);
          setPipelineName(name);
        },
        onError: (e) => toastApiError(e, "Failed to save the pipeline"),
      },
    );
  }, [pipelineName, serialize, createMutation]);

  const isModal = mode === "modal";

  const header = isModal ? (
    /* Modal header */
    <div className="h-14 shrink-0 border-b border-white/[0.08] flex items-center justify-between px-4 bg-surface">
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={onClose}
          className="p-1.5 rounded-md hover:bg-white/[0.06] text-white/50 hover:text-white/80 transition-colors"
        >
          <X size={18} />
        </button>
        <h2 className="text-section font-semibold">Pipeline editor</h2>
      </div>
      <div className="flex items-center gap-2">
        <span className="relative inline-flex">
          <button
            type="button"
            onClick={handleAutoArrange}
            disabled={nodes.length < 2}
            className="flex items-center gap-1.5 text-xs text-white/50 hover:text-white/70 disabled:opacity-30 disabled:hover:text-white/50 px-3 py-1.5 rounded-md hover:bg-white/[0.04] transition-colors"
            title="Re-position all nodes into a clean left-to-right layout"
          >
            <LayoutGrid size={13} />
            Auto arrange
          </button>
          <span className="pointer-events-none absolute inset-0 flex items-center justify-center">
            <DivineRipple key={rippleKey} size={120} />
          </span>
        </span>
        <button
          type="button"
          onClick={() => setShowTemplatePicker(true)}
          className="text-xs text-white/50 hover:text-white/70 px-3 py-1.5 rounded-md hover:bg-white/[0.04] transition-colors"
        >
          Load template
        </button>
        <button
          type="button"
          onClick={handleApply}
          disabled={nodes.length === 0}
          className="h-[30px] rounded-md bg-primary px-3.5 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary-hover disabled:opacity-30"
        >
          Apply pipeline
        </button>
      </div>
    </div>
  ) : (
    /* Page header. On a phone it wraps: the name on a row of its own, the
       actions under it, so Templates, Save and Save As stay in view. */
    <div
      data-testid="pipeline-toolbar"
      className="shrink-0 border-b border-white/[0.08] flex flex-wrap items-center justify-between gap-x-3 gap-y-2 px-4 py-2 bg-surface sm:h-14 sm:flex-nowrap sm:py-0"
    >
      <div className="flex w-full flex-col sm:w-auto">
        <input
          type="text"
          value={pipelineName}
          onChange={(e) => setPipelineName(e.target.value)}
          className="bg-transparent border-none text-base font-semibold text-white outline-none focus:ring-1 focus:ring-white/20 rounded px-1.5 py-0.5 -ml-1.5 w-full sm:w-64"
          placeholder="Pipeline name..."
        />
        {isBuiltin && (
          <p id={builtinHintId} className="text-xs text-fg-muted">
            {BUILTIN_HINT}
          </p>
        )}
      </div>
      <div data-testid="pipeline-actions" className="flex flex-wrap items-center gap-2">
        <span className="relative inline-flex">
          <button
            type="button"
            onClick={handleAutoArrange}
            disabled={nodes.length < 2}
            className="flex items-center gap-1.5 text-xs text-white/50 hover:text-white/70 disabled:opacity-30 disabled:hover:text-white/50 px-3 py-1.5 rounded-md hover:bg-white/[0.04] transition-colors"
            title="Re-position all nodes into a clean left-to-right layout"
          >
            <LayoutGrid size={13} />
            Auto arrange
          </button>
          <span className="pointer-events-none absolute inset-0 flex items-center justify-center">
            <DivineRipple key={rippleKey} size={120} />
          </span>
        </span>
        <button
          type="button"
          onClick={() => setShowTemplateDrawer(true)}
          className="flex items-center gap-1.5 text-xs text-white/50 hover:text-white/70 px-3 py-1.5 rounded-md hover:bg-white/[0.04] transition-colors"
        >
          <FolderOpen size={13} />
          Templates
        </button>
        {/* One gold action: Save for a template of your own, Save As for a
            new pipeline or a built-in, which only a copy can keep. The two
            wrap as a pair, so on a phone they share the actions' second row. */}
        <div className="flex items-center gap-2">
          <Button
            type="button"
            size="sm"
            variant={canSaveInPlace ? "default" : "outline"}
            onClick={handleSave}
            disabled={!canSaveInPlace || nodes.length === 0 || renameMutation.isPending}
            aria-describedby={isBuiltin ? builtinHintId : undefined}
            title={isBuiltin ? BUILTIN_HINT : undefined}
            className="gap-1.5"
          >
            <Save size={13} />
            Save
          </Button>
          <Button
            type="button"
            size="sm"
            variant={canSaveInPlace ? "outline" : "default"}
            onClick={handleSaveAs}
            disabled={nodes.length === 0 || createMutation.isPending}
          >
            Save As
          </Button>
        </div>
      </div>
    </div>
  );

  const content = (
    <>
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
        <TemplatePicker
          onSelect={(template) => loadGraph(template.graph)}
          onClose={() => setShowTemplatePicker(false)}
        />
      )}
    </>
  );

  // The modal editor covers the window, so it renders over it like a dialog.
  return isModal ? (
    <Overlay className="bg-page flex flex-col p-0">{content}</Overlay>
  ) : (
    <div className="flex flex-col h-full bg-page">{content}</div>
  );
}
