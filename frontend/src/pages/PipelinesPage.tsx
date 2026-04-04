import { useState, useCallback } from "react";
import { Plus, GitBranch, Calendar, ChevronRight } from "lucide-react";
import { PipelineEditor } from "@/components/pipeline-editor/PipelineEditor";
import { useTemplates } from "@/hooks/usePipelines";
import { fetchTemplate } from "@/api/pipelines";
import type { PipelineGraph, PipelineTemplateSummary } from "@/lib/types";

function formatDate(dateStr: string) {
  return new Date(dateStr).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

interface TemplateCardProps {
  template: PipelineTemplateSummary;
  onOpen: (template: PipelineTemplateSummary) => void;
  loading: boolean;
}

function TemplateCard({ template, onOpen, loading }: TemplateCardProps) {
  return (
    <button
      type="button"
      onClick={() => onOpen(template)}
      disabled={loading}
      className="group w-full text-left rounded-xl border border-white/[0.06] bg-white/[0.02] hover:bg-white/[0.05] hover:border-white/[0.1] p-4 transition-all disabled:opacity-50 disabled:cursor-wait"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-3 min-w-0">
          <div className="shrink-0 w-8 h-8 rounded-lg bg-blue-500/10 border border-blue-500/20 flex items-center justify-center">
            <GitBranch size={14} className="text-blue-400" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-medium text-white/90 truncate">{template.name}</p>
            {template.description && (
              <p className="text-xs text-white/40 mt-0.5 line-clamp-1">{template.description}</p>
            )}
          </div>
        </div>
        <ChevronRight
          size={14}
          className="shrink-0 mt-0.5 text-white/20 group-hover:text-white/50 transition-colors"
        />
      </div>
      <div className="flex items-center gap-1 mt-3 text-[10px] text-white/25">
        <Calendar size={10} />
        <span>{formatDate(template.created_at)}</span>
      </div>
    </button>
  );
}

function LandingView({
  onNewPipeline,
  onOpenTemplate,
}: {
  onNewPipeline: () => void;
  onOpenTemplate: (template: PipelineTemplateSummary) => void;
}) {
  const { data: templates, isLoading } = useTemplates();
  const [loadingId, setLoadingId] = useState<string | null>(null);

  const handleOpen = useCallback(
    async (template: PipelineTemplateSummary) => {
      setLoadingId(template.template_id);
      try {
        const full = await fetchTemplate(template.template_id);
        onOpenTemplate({ ...template, ...full });
      } finally {
        setLoadingId(null);
      }
    },
    [onOpenTemplate],
  );

  return (
    <div className="flex flex-col h-full">
      {/* Page header */}
      <div className="shrink-0 flex items-center justify-between px-6 py-5 border-b border-white/[0.06]">
        <div>
          <h1 className="text-xl font-semibold text-white">Pipelines</h1>
          <p className="text-xs text-white/40 mt-0.5">Design and manage agent pipeline templates</p>
        </div>
        <button
          type="button"
          onClick={onNewPipeline}
          className="flex items-center gap-2 bg-blue-600 hover:bg-blue-500 text-white text-sm font-medium px-4 py-2 rounded-lg transition-colors"
        >
          <Plus size={15} />
          New Pipeline
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-6 py-6">
        {isLoading && (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {[...Array(3)].map((_, i) => (
              <div
                key={i}
                className="h-24 rounded-xl border border-white/[0.06] bg-white/[0.02] animate-pulse"
              />
            ))}
          </div>
        )}

        {!isLoading && (!templates || templates.length === 0) && (
          <div className="flex flex-col items-center justify-center h-64 text-center">
            <div className="w-12 h-12 rounded-2xl bg-white/[0.04] border border-white/[0.08] flex items-center justify-center mb-4">
              <GitBranch size={20} className="text-white/30" />
            </div>
            <p className="text-sm font-medium text-white/50">No pipeline templates yet</p>
            <p className="text-xs text-white/25 mt-1">Click "New Pipeline" to create your first one</p>
          </div>
        )}

        {!isLoading && templates && templates.length > 0 && (
          <>
            <p className="text-xs text-white/30 uppercase tracking-wider font-medium mb-3">
              Saved Templates ({templates.length})
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {templates.map((t) => (
                <TemplateCard
                  key={t.template_id}
                  template={t}
                  onOpen={handleOpen}
                  loading={loadingId === t.template_id}
                />
              ))}
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export function PipelinesPage() {
  // null = landing, PipelineGraph = editor with that graph, "new" = editor with empty canvas
  const [editorGraph, setEditorGraph] = useState<PipelineGraph | null | "new">(null);

  const handleOpenTemplate = useCallback((templateWithGraph: PipelineTemplateSummary & { graph?: PipelineGraph }) => {
    if (templateWithGraph.graph) {
      setEditorGraph(templateWithGraph.graph);
    }
  }, []);

  const handleNewPipeline = useCallback(() => {
    setEditorGraph("new");
  }, []);

  const handleClose = useCallback(() => {
    setEditorGraph(null);
  }, []);

  if (editorGraph !== null) {
    return (
      <div className="h-full">
        <PipelineEditor
          mode="page"
          initialGraph={editorGraph === "new" ? undefined : editorGraph}
          onClose={handleClose}
        />
      </div>
    );
  }

  return (
    <LandingView
      onNewPipeline={handleNewPipeline}
      onOpenTemplate={handleOpenTemplate as (t: PipelineTemplateSummary) => void}
    />
  );
}
