import { useQuery, useMutation, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { fetchTemplates, fetchTemplate, createTemplate, deleteTemplate, renameTemplate } from "@/api/pipelines";
import type { PipelineGraph } from "@/lib/types";

export function useTemplates() {
  return useQuery({
    queryKey: ["pipeline-templates"],
    queryFn: fetchTemplates,
  });
}

export function useTemplate(templateId: string | null) {
  return useQuery({
    queryKey: ["pipeline-template", templateId],
    queryFn: () => fetchTemplate(templateId!),
    enabled: !!templateId,
  });
}

/** A write to one template stales both the list and its detail query, so a
 * template reopened after a save shows the saved name and graph. */
function invalidateTemplate(qc: QueryClient, templateId: string) {
  qc.invalidateQueries({ queryKey: ["pipeline-templates"] });
  qc.invalidateQueries({ queryKey: ["pipeline-template", templateId] });
}

export function useCreateTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, description, graph }: { name: string; description: string; graph: PipelineGraph }) =>
      createTemplate(name, description, graph),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["pipeline-templates"] }),
  });
}

export function useDeleteTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (templateId: string) => deleteTemplate(templateId),
    onSuccess: (_data, templateId) => invalidateTemplate(qc, templateId),
  });
}

export function useRenameTemplate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (params: { templateId: string; name?: string; description?: string; graph?: PipelineGraph }) =>
      renameTemplate(params.templateId, params.name, params.description, params.graph),
    onSuccess: (_data, { templateId }) => invalidateTemplate(qc, templateId),
  });
}
