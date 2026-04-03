import { useState, useRef, useEffect } from "react";
import { X, MoreVertical, Copy, Pencil, Trash2 } from "lucide-react";
import { fetchTemplate } from "@/api/pipelines";
import { useTemplates, useTemplate, useRenameTemplate, useCreateTemplate, useDeleteTemplate } from "@/hooks/usePipelines";
import type { PipelineGraph, PipelineTemplateSummary } from "@/lib/types";

interface TemplateDrawerProps {
  onSelect: (graph: PipelineGraph) => void;
  onClose: () => void;
}

export function TemplateDrawer({ onSelect, onClose }: TemplateDrawerProps) {
  const { data: templates, isLoading } = useTemplates();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const { data: selectedTemplate } = useTemplate(selectedId);
  const renameMutation = useRenameTemplate();
  const createMutation = useCreateTemplate();
  const deleteMutation = useDeleteTemplate();

  const [menuOpenId, setMenuOpenId] = useState<string | null>(null);
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const renameInputRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  // Close dropdown when clicking outside
  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as HTMLElement)) {
        setMenuOpenId(null);
      }
    }
    if (menuOpenId) {
      document.addEventListener("mousedown", handleClick);
      return () => document.removeEventListener("mousedown", handleClick);
    }
  }, [menuOpenId]);

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
      onSelect(selectedTemplate.graph);
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
    setMenuOpenId(null);
  };

  const handleRenameSubmit = (templateId: string) => {
    const trimmed = renameValue.trim();
    if (trimmed && trimmed !== templates?.find((t) => t.template_id === templateId)?.name) {
      renameMutation.mutate({ templateId, name: trimmed });
    }
    setRenamingId(null);
  };

  const handleDuplicate = async (t: PipelineTemplateSummary) => {
    setMenuOpenId(null);
    // Fetch the full template to get its graph for duplication
    const full = await fetchTemplate(t.template_id);
    createMutation.mutate({
      name: `${t.name} (copy)`,
      description: t.description,
      graph: full.graph,
    });
  };

  const handleDelete = (templateId: string) => {
    setMenuOpenId(null);
    if (!window.confirm("Delete this template?")) return;
    deleteMutation.mutate(templateId);
  };

  const formatDate = (dateStr: string) => {
    const d = new Date(dateStr);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  };

  return (
    <div className="absolute inset-y-0 left-0 z-30 w-[260px] bg-[#131520] border-r border-white/[0.08] flex flex-col shadow-2xl animate-in slide-in-from-left duration-200">
      {/* Header */}
      <div className="h-12 shrink-0 flex items-center justify-between px-3 border-b border-white/[0.08]">
        <span className="text-xs font-semibold text-white/70 uppercase tracking-wider">Templates</span>
        <button
          type="button"
          onClick={onClose}
          className="p-1 rounded-md hover:bg-white/[0.06] text-white/40 hover:text-white/70 transition-colors"
        >
          <X size={14} />
        </button>
      </div>

      {/* Template list */}
      <div className="flex-1 overflow-y-auto p-2 space-y-1">
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
                value={renameValue}
                onChange={(e) => setRenameValue(e.target.value)}
                onBlur={() => handleRenameSubmit(t.template_id)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleRenameSubmit(t.template_id);
                  if (e.key === "Escape") setRenamingId(null);
                }}
                className="w-full bg-white/[0.06] border border-white/20 rounded px-1.5 py-0.5 text-xs text-white outline-none"
                onClick={(e) => e.stopPropagation()}
              />
            ) : (
              <div className="flex items-center justify-between">
                <span className="text-xs font-medium text-white/80 truncate">{t.name}</span>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    setMenuOpenId(menuOpenId === t.template_id ? null : t.template_id);
                  }}
                  className="p-0.5 rounded opacity-0 group-hover:opacity-100 hover:bg-white/[0.08] text-white/40 hover:text-white/70 transition-all"
                >
                  <MoreVertical size={12} />
                </button>
              </div>
            )}

            {/* Description + meta */}
            {renamingId !== t.template_id && (
              <>
                {t.description && (
                  <p className="text-[10px] text-white/30 mt-1 line-clamp-2 leading-relaxed">{t.description}</p>
                )}
                <div className="flex items-center gap-2 mt-1.5 text-[10px] text-white/25">
                  <span>{formatDate(t.created_at)}</span>
                </div>
              </>
            )}

            {/* Context menu dropdown */}
            {menuOpenId === t.template_id && (
              <div
                ref={menuRef}
                className="absolute right-2 top-8 z-40 w-32 rounded-md border border-white/[0.1] bg-[#1a1d2e] shadow-xl py-1"
                onClick={(e) => e.stopPropagation()}
              >
                <button
                  type="button"
                  onClick={() => handleRenameStart(t)}
                  className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-white/60 hover:bg-white/[0.06] hover:text-white/80 transition-colors"
                >
                  <Pencil size={11} /> Rename
                </button>
                <button
                  type="button"
                  onClick={() => handleDuplicate(t)}
                  className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-white/60 hover:bg-white/[0.06] hover:text-white/80 transition-colors"
                >
                  <Copy size={11} /> Duplicate
                </button>
                <button
                  type="button"
                  onClick={() => handleDelete(t.template_id)}
                  className="w-full flex items-center gap-2 px-3 py-1.5 text-xs text-red-400/70 hover:bg-red-500/10 hover:text-red-400 transition-colors"
                >
                  <Trash2 size={11} /> Delete
                </button>
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
