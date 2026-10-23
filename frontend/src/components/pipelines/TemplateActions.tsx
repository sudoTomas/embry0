import { useState } from "react";
import { toastApiError } from "@/api/errorToast";
import { useSetTemplateDefault } from "@/hooks/usePipelines";
import { Menu, type MenuItem } from "@/components/ui/Menu";
import { DuplicateTemplateDialog } from "@/components/pipelines/DuplicateTemplateDialog";
import { TEMPLATE_KINDS, type PipelineTemplateSummary } from "@/lib/types";

/**
 * A saved template's menu on the Pipelines page (spec 3.7): duplicate it
 * under a new name, make it the default route for a kind, or clear the kind
 * it holds. It sits beside the card, which is itself a button, so a choice
 * here never opens the template.
 */
export function TemplateActions({ template }: { template: PipelineTemplateSummary }) {
  const [duplicating, setDuplicating] = useState(false);
  const setDefault = useSetTemplateDefault();
  const held = template.default_for_kind ?? null;

  const items: MenuItem[] = [{ label: "Duplicate…", onSelect: () => setDuplicating(true) }];
  if (held) {
    items.push({
      label: `Clear default (${held})`,
      onSelect: () =>
        setDefault.mutate({ templateId: template.template_id, kind: null }, { onError: (e) => toastApiError(e, "Could not clear the default") }),
    });
  }
  for (const kind of TEMPLATE_KINDS) {
    if (kind === held) continue;
    items.push({
      label: `Make default for ${kind}`,
      onSelect: () =>
        setDefault.mutate({ templateId: template.template_id, kind }, { onError: (e) => toastApiError(e, "Could not change the default") }),
    });
  }

  return (
    <>
      <Menu label={`Actions for ${template.name}`} items={items} triggerClassName="h-7 w-7" />
      {duplicating && <DuplicateTemplateDialog template={template} onClose={() => setDuplicating(false)} />}
    </>
  );
}
