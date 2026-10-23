import { useState, type FormEvent } from "react";
import { toast } from "sonner";
import { apiErrorMessage } from "@/api/errorDetail";
import { toastApiError } from "@/api/errorToast";
import { useDuplicateTemplate } from "@/hooks/usePipelines";
import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import type { PipelineTemplateSummary } from "@/lib/types";

/** Whether the server refused the name as one another template already has (HTTP 409). */
function isNameTaken(error: unknown): boolean {
  return (error as { response?: { status?: unknown } } | null)?.response?.status === 409;
}

/**
 * Name the copy (spec 3.7): POST /pipelines/templates/{id}/duplicate wants a
 * unique name. A name the server refuses as taken is said under the field,
 * which keeps what was typed, so the person corrects it in place.
 */
export function DuplicateTemplateDialog({ template, onClose }: { template: PipelineTemplateSummary; onClose: () => void }) {
  const [name, setName] = useState(`${template.name} (copy)`);
  const [error, setError] = useState<string | null>(null);
  const duplicate = useDuplicateTemplate();

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Name the copy.");
      return;
    }
    duplicate.mutate(
      { templateId: template.template_id, name: trimmed },
      {
        onSuccess: (copy) => {
          toast.success(`Duplicated as ${copy.name}`);
          onClose();
        },
        onError: (err) => {
          if (isNameTaken(err)) setError(apiErrorMessage(err));
          else toastApiError(err, "Failed to duplicate");
        },
      },
    );
  };

  return (
    <Dialog onClose={onClose} title="Duplicate template" className="max-w-md">
      <form onSubmit={submit} className="space-y-4 p-5">
        <div className="space-y-1">
          <Label htmlFor="duplicate-template-name">Name</Label>
          <Input
            id="duplicate-template-name"
            value={name}
            onChange={(e) => {
              setError(null);
              setName(e.target.value);
            }}
            maxLength={100}
            disabled={duplicate.isPending}
            data-autofocus
          />
        </div>
        {error && (
          <p className="text-sm text-destructive" role="alert">
            {error}
          </p>
        )}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={onClose} disabled={duplicate.isPending}>
            Cancel
          </Button>
          <Button type="submit" disabled={duplicate.isPending}>
            {duplicate.isPending ? "Duplicating…" : "Duplicate"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
