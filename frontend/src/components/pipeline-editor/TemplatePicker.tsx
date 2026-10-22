import { useTemplates, useTemplate } from "@/hooks/usePipelines";
import { useState } from "react";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import type { PipelineTemplate } from "@/lib/types";

interface TemplatePickerProps {
  onSelect: (template: PipelineTemplate) => void;
  onClose: () => void;
}

export function TemplatePicker({ onSelect, onClose }: TemplatePickerProps) {
  const { data: templates, isLoading } = useTemplates();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const { data: selectedTemplate } = useTemplate(selectedId);

  const handleApply = () => {
    if (selectedTemplate) {
      onSelect(selectedTemplate);
      onClose();
    }
  };

  return (
    <Dialog onClose={onClose} title="Load pipeline template" className="max-w-[500px]">
      <div className="flex max-h-[400px] flex-col">
        <div className="flex-1 overflow-y-auto p-2">
          {isLoading && <div className="p-4 text-sm text-white/40">Loading...</div>}
          {!isLoading && (!templates || templates.length === 0) && (
            <div className="p-4 text-sm text-white/40">No templates saved yet.</div>
          )}
          {templates?.map((t) => (
            <button
              key={t.template_id}
              type="button"
              onClick={() => setSelectedId(t.template_id)}
              aria-pressed={selectedId === t.template_id}
              className={`mb-1 w-full rounded-md px-3 py-2 text-left text-sm transition-colors ${
                selectedId === t.template_id ? "bg-primary/20 text-white" : "text-white/70 hover:bg-white/5"
              }`}
            >
              <div className="font-medium">{t.name}</div>
              <div className="text-xs text-white/40">{t.description}</div>
            </button>
          ))}
        </div>
        <div className="flex items-center justify-end gap-2 border-t border-white/[0.08] p-3">
          <Button type="button" variant="outline" size="sm" onClick={onClose}>
            Cancel
          </Button>
          <Button type="button" size="sm" onClick={handleApply} disabled={!selectedTemplate}>
            Load template
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
