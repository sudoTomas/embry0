import { useMutation, useQueryClient } from "@tanstack/react-query";
import { bootstrapRepo, type BootstrapRequest } from "@/api/bootstrap";

export function useBootstrapRepo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (req: BootstrapRequest) => bootstrapRepo(req),
    onSuccess: (result) => {
      // Keyed by the full repo path, as useQaConfig and useRepoPreferences are.
      qc.invalidateQueries({ queryKey: ["qa-config", result.repo] });
      qc.invalidateQueries({ queryKey: ["repo-preferences", result.repo] });
      // QA dashboard repo lists don't know about the new repo yet.
      qc.invalidateQueries({ queryKey: ["qa-dashboard"] });
      // Bootstrap registers the repo (qa_configs upsert auto-registers), so
      // the registry list and the discoverable set both changed (RAV-1301).
      qc.invalidateQueries({ queryKey: ["repos"] });
      qc.invalidateQueries({ queryKey: ["github", "discoverable-repos"] });
    },
  });
}
