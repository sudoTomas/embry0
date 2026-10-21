import { useState, useRef, useEffect } from "react";
import { Copy, Pencil, Trash2 } from "lucide-react";
import { fetchTemplate } from "@/api/pipelines";
import { formatDay } from "@/lib/utils";
import { useTemplates, useTemplate, useRenameTemplate, useCreateTemplate, useDeleteTemplate } from "@/hooks/usePipelines";
import type { PipelineTemplate, PipelineTemplateSummary } from "@/lib/types";
import { toastApiError } from "@/api/errorToast";
import { Dialog } from "@/components/ui/Dialog";
import { Menu } from "@/components/ui/Menu";

interface TemplateDrawerProps {
  onSelect: (template: PipelineTemplate) => void;
  onClose: () => void;
}

export function TemplateDrawer({ onSelect, onClose }: TemplateDrawerProps) {
  const { data: templates, isLoading } = useTemplates();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const { data: selectedTemplate } = useTemplate(selectedId);
  const renameMutation = useRenameTemplate();
  const createMutation = useCreateTemplate();
  const deleteMutation = useDeleteTemplate();

  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const renameInputRef = useRef<HTMLInputElement>(null);

  // Focus rename input when entering rename mode
  useEffect(() => {
    if (renamingId && renameInputRef.current) {
      renameInputRef.current.focus();
      renameInputRef.current.select();
    }
  }, [renamingId]);

  const loadedRef = useRef(false);

  // Reset loaded flag when selection changes
  useEffect(() => {
    loadedRef.current = false;
  }, [selectedId]);

  // When a template is fetched and selectedId is set, load it (only once per selection)
  useEffect(() => {
    if (selectedTemplate && selectedId && !loadedRef.current) {
      loadedRef.current = true;
      onSelect(selectedTemplate);
      onClose();
    }
  }, [selectedTemplate, selectedId, onSelect, onClose]);

  const handleCardClick = (templateId: string) => {
    if (renamingId) return;
    setSelectedId(templateId);
  };

  const handleRenameStart = (t: PipelineTemplateSummary) => {
    setRenamingId(t.template_id);
    setRenameValue(t.name);
  };

  const handleRenameSubmit = (templateId: string) => {
    const trimmed = renameValue.trim();
    if (trimmed && trimmed !== templates?.find((t) => t.template_id === templateId)?.name) {
      renameMutation.mutate({ templateId, name: trimmed }, { onError: (e) => toastApiError(e, "Failed to rename") });
    }
    setRenamingId(null);
  };

  const handleDuplicate = async (t: PipelineTemplateSummary) => {
    // Fetch the full template to get its graph for duplication
    const full = await fetchTemplate(t.template_id);
    createMutation.mutate(
      { name: `${t.name} (copy)`, description: t.description, graph: full.graph },
      { onError: (e) => toastApiError(e, "Failed to duplicate") },
    );
  };

  const handleDelete = (templateId: string) => {
    if (!window.confirm("Delete this template?")) return;
    deleteMutation.mutate(templateId, { onError: (e) => toastApiError(e, "Failed to delete") });
  };

  return (
    <Dialog onClose={onClose} title="Templates" side="left" className="w-[260px] max-w-[85vw]">
      <div className="flex-1 space-y-1 overflow-y-auto p-2">
        {isLoading && <div className="text-xs text-white/30 p-3">Loading templates...</div>}
        {!isLoading && (!templates || templates.length === 0) && (
          <div className="text-xs text-white/30 p-3 text-center">
            No templates saved yet. Use Save As to create one.
          </div>
        )}
        {templates?.map((t) => (
          <div
            key={t.template_id}
            onClick={() => handleCardClick(t.template_id)}
            className="group relative rounded-md border border-white/[0.06] bg-white/[0.02] hover:bg-white/[0.05] px-3 py-2.5 cursor-pointer transition-colors"
          >
            {/* Name / rename input */}
            {renamingId === t.template_id ? (
              <input
                ref={renameInputRef}
                aria-label="Template name"
                value={renameValue}
                onChange={(e) => setRenameValue(e.target.value)}
                onBlur={() => handleRenameSubmit(t.template_id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleRenameSubmit(t.template_id);
                  if (e.key === "Escape") {
                    e.stopPropagation(); // ours, not the drawer's
                    setRenamingId(null);
                  }
                }}
                className="w-full rounded border border-white/20 bg-white/[0.06] px-1.5 py-0.5 text-xs text-white outline-none"
                onClick={(e) => e.stopPropagation()}
              />
            ) : (
              <div className="flex items-center justify-between">
                <span className="truncate text-xs font-medium text-white/80">{t.name}</span>
                <Menu
                  label={`Actions for ${t.name}`}
                  triggerClassName="opacity-0 transition-opacity group-hover:opacity-100 focus:opacity-100 group-focus-within:opacity-100"
                  items={[
                    { label: "Rename", icon: <Pencil size={11} />, onSelect: () => handleRenameStart(t) },
                    { label: "Duplicate", icon: <Copy size={11} />, onSelect: () => void handleDuplicate(t) },
                    { label: "Delete", icon: <Trash2 size={11} />, destructive: true, onSelect: () => handleDelete(t.template_id) },
                  ]}
                />
              </div>
            )}

            {/* Description + meta */}
            {renamingId !== t.template_id && (
              <>
                {t.description && (
                  <p className="text-[10px] text-white/30 mt-1 line-clamp-2 leading-relaxed">{t.description}</p>
                )}
                <div className="flex items-center gap-2 mt-1.5 text-[10px] text-white/25">
                  <span>{formatDay(t.created_at)}</span>
                </div>
              </>
            )}
          </div>
        ))}
      </div>
    </Dialog>
  );
}
