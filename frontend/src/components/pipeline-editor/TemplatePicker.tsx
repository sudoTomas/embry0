import { useTemplates, useTemplate } from "@/hooks/usePipelines";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import type { PipelineGraph } from "@/lib/types";

interface TemplatePickerProps {
  onSelect: (graph: PipelineGraph) => void;
  onClose: () => void;
}

export function TemplatePicker({ onSelect, onClose }: TemplatePickerProps) {
  const { data: templates, isLoading } = useTemplates();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const { data: selectedTemplate } = useTemplate(selectedId);

  const handleApply = () => {
    if (selectedTemplate) {
      onSelect(selectedTemplate.graph);
      onClose();
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="w-[500px] max-h-[400px] rounded-lg border border-white/10 bg-[#161822] shadow-xl overflow-hidden flex flex-col">
        <div className="p-4 border-b border-white/[0.08]">
          <h3 className="text-sm font-semibold text-white">
            Load Pipeline Template
          </h3>
        </div>
        <div className="flex-1 overflow-y-auto p-2">
          {isLoading && (
            <div className="text-sm text-white/40 p-4">Loading...</div>
          )}
          {!isLoading && (!templates || templates.length === 0) && (
            <div className="text-sm text-white/40 p-4">
              No templates saved yet.
            </div>
          )}
          {templates?.map((t) => (
            <button
              key={t.template_id}
              type="button"
              onClick={() => setSelectedId(t.template_id)}
              className={`w-full text-left px-3 py-2 rounded-md mb-1 text-sm transition-colors ${
                selectedId === t.template_id
                  ? "bg-primary/20 text-white"
                  : "text-white/70 hover:bg-white/5"
              }`}
            >
              <div className="font-medium">{t.name}</div>
              <div className="text-xs text-white/40">{t.description}</div>
            </button>
          ))}
        </div>
        <div className="flex items-center justify-end gap-2 p-3 border-t border-white/[0.08]">
          <Button type="button" variant="outline" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={handleApply}
            disabled={!selectedTemplate}
          >
            Load Template
          </Button>
        </div>
      </div>
    </div>
  );
}
