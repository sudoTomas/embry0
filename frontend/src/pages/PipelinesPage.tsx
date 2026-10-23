import { useState, useCallback } from "react";
import { Plus, GitBranch, Calendar } from "lucide-react";
import { PipelineEditor } from "@/components/pipeline-editor/PipelineEditor";
import { TemplateActions } from "@/components/pipelines/TemplateActions";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { formatDay } from "@/lib/utils";
import { useTemplates } from "@/hooks/usePipelines";
import { fetchTemplate } from "@/api/pipelines";
import type { PipelineTemplate, PipelineTemplateSummary } from "@/lib/types";

interface TemplateCardProps {
  template: PipelineTemplateSummary;
  onOpen: (template: PipelineTemplateSummary) => void;
  loading: boolean;
}

function TemplateCard({ template, onOpen, loading }: TemplateCardProps) {
  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => onOpen(template)}
        disabled={loading}
        className="group w-full text-left rounded-lg border border-border bg-surface hover:bg-overlay hover:border-border-strong p-4 pr-12 transition-colors disabled:opacity-50 disabled:cursor-wait"
      >
        <div className="flex items-center gap-3 min-w-0">
          <div className="shrink-0 w-8 h-8 rounded-lg bg-overlay border border-border flex items-center justify-center">
            <GitBranch size={14} className="text-fg-secondary" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-medium text-white/90 truncate">{template.name}</p>
            {template.description && (
              <p className="text-xs text-white/40 mt-0.5 line-clamp-1">{template.description}</p>
            )}
          </div>
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-2xs text-fg-muted">
          <Calendar size={10} />
          <span>{formatDay(template.created_at)}</span>
          {template.is_builtin && <Badge tone="neutral">Built in</Badge>}
          {template.default_for_kind && <Badge tone="info">{`Default for ${template.default_for_kind}`}</Badge>}
        </div>
      </button>
      <div className="absolute right-2 top-2">
        <TemplateActions template={template} />
      </div>
    </div>
  );
}

function LandingView({
  onNewPipeline,
  onOpenTemplate,
}: {
  onNewPipeline: () => void;
  onOpenTemplate: (template: PipelineTemplate) => void;
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

  // The shared page frame: the content padding comes from the layout, the
  // header is the 20px title with the primary action on the right, and a
  // section title is 15px sentence case.
  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="title-pixel text-xl font-bold">Pipelines</h1>
          <p className="text-sm text-muted-foreground">Design and manage agent pipeline templates</p>
        </div>
        <Button type="button" onClick={onNewPipeline} data-testid="new-pipeline">
          <Plus size={15} />
          New pipeline
        </Button>
      </div>

      {isLoading && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {[...Array(3)].map((_, i) => (
            <div key={i} className="h-24 rounded-lg border border-border bg-surface motion-safe:animate-pulse" />
          ))}
        </div>
      )}

      {!isLoading && (!templates || templates.length === 0) && (
        <div className="flex flex-col items-center justify-center h-64 text-center">
          <div className="w-12 h-12 rounded-lg bg-overlay border border-border flex items-center justify-center mb-4">
            <GitBranch size={20} className="text-fg-muted" />
          </div>
          <p className="text-sm font-medium text-fg-secondary">No pipeline templates yet</p>
          <p className="text-xs text-fg-muted mt-1">Choose New pipeline to create your first one</p>
        </div>
      )}

      {!isLoading && templates && templates.length > 0 && (
        <section aria-labelledby="saved-templates-heading" className="space-y-3">
          <h2 id="saved-templates-heading" className="text-section font-semibold">
            Saved templates <span className="ml-1 font-normal tabular-nums text-fg-muted">{templates.length}</span>
          </h2>
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
        </section>
      )}
    </div>
  );
}

export function PipelinesPage() {
  // null = landing, a template = editor on that saved template (its name and
  // id travel with the graph so Save writes back to it), "new" = empty canvas
  const [editing, setEditing] = useState<PipelineTemplate | null | "new">(null);

  const handleOpenTemplate = useCallback((template: PipelineTemplate) => {
    if (template.graph) {
      setEditing(template);
    }
  }, []);

  const handleNewPipeline = useCallback(() => {
    setEditing("new");
  }, []);

  const handleClose = useCallback(() => {
    setEditing(null);
  }, []);

  if (editing !== null) {
    return (
      <div className="h-full">
        <PipelineEditor
          mode="page"
          initialGraph={editing === "new" ? undefined : editing.graph}
          initialTemplate={editing === "new" ? undefined : editing}
          onClose={handleClose}
        />
      </div>
    );
  }

  return <LandingView onNewPipeline={handleNewPipeline} onOpenTemplate={handleOpenTemplate} />;
}
