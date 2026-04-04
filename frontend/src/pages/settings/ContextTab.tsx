import { useState, useEffect, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Textarea } from "@/components/ui/Textarea";
import { PageError } from "@/components/PageError";
import { toast } from "sonner";
import { Trash2, Plus } from "lucide-react";
import {
  fetchGlobalContext,
  updateGlobalContext,
  fetchRepoContexts,
  updateRepoContext,
  deleteRepoContext,
  type ContextConfig,
  type RepoContext,
} from "@/api/config";

export default function ContextTab() {
  const qc = useQueryClient();

  // Global context
  const {
    data: globalContext,
    isLoading: globalLoading,
    isError: globalError,
    refetch: refetchGlobal,
  } = useQuery({
    queryKey: ["global-context"],
    queryFn: fetchGlobalContext,
  });

  const [globalForm, setGlobalForm] = useState<ContextConfig | null>(null);
  const [globalSaving, setGlobalSaving] = useState(false);

  useEffect(() => {
    if (globalContext) {
      setGlobalForm({ ...globalContext });
    }
  }, [globalContext]);

  // Per-repo contexts
  const {
    data: repoContexts,
    isLoading: reposLoading,
    isError: reposError,
    refetch: refetchRepos,
  } = useQuery({
    queryKey: ["repo-contexts"],
    queryFn: fetchRepoContexts,
  });

  const [repoForms, setRepoForms] = useState<RepoContext[]>([]);
  const [addingRepo, setAddingRepo] = useState(false);
  const [newRepoName, setNewRepoName] = useState("");

  useEffect(() => {
    if (repoContexts) {
      setRepoForms(repoContexts.map((r) => ({ ...r })));
    }
  }, [repoContexts]);

  const handleSaveGlobal = useCallback(async () => {
    if (!globalForm) return;
    setGlobalSaving(true);
    try {
      await updateGlobalContext(globalForm);
      qc.invalidateQueries({ queryKey: ["global-context"] });
      toast.success("Global context saved");
    } catch (e) {
      toast.error(`Failed: ${e instanceof Error ? e.message : "Unknown error"}`);
    } finally {
      setGlobalSaving(false);
    }
  }, [globalForm, qc]);

  const handleRepoFieldChange = (index: number, field: "system_context" | "assistant_context", value: string) => {
    setRepoForms((prev) => {
      const next = [...prev];
      next[index] = { ...next[index], [field]: value };
      return next;
    });
  };

  const handleSaveRepo = async (index: number) => {
    const repo = repoForms[index];
    try {
      await updateRepoContext(repo.repo, {
        system_context: repo.system_context,
        assistant_context: repo.assistant_context,
      });
      qc.invalidateQueries({ queryKey: ["repo-contexts"] });
      toast.success(`Context saved for ${repo.repo}`);
    } catch (e) {
      toast.error(`Failed: ${e instanceof Error ? e.message : "Unknown error"}`);
    }
  };

  const handleDeleteRepo = async (repo: string) => {
    try {
      await deleteRepoContext(repo);
      qc.invalidateQueries({ queryKey: ["repo-contexts"] });
      toast.success(`Context removed for ${repo}`);
    } catch (e) {
      toast.error(`Failed: ${e instanceof Error ? e.message : "Unknown error"}`);
    }
  };

  const handleAddRepo = async () => {
    const trimmed = newRepoName.trim();
    if (!trimmed) return;
    if (!/^[^/]+\/[^/]+$/.test(trimmed)) {
      toast.error("Repository must be in owner/repo format");
      return;
    }
    if (repoForms.some((r) => r.repo === trimmed)) {
      toast.error("Repository already has context configured");
      return;
    }
    try {
      await updateRepoContext(trimmed, { system_context: "", assistant_context: "" });
      qc.invalidateQueries({ queryKey: ["repo-contexts"] });
      setNewRepoName("");
      setAddingRepo(false);
      toast.success(`Added context for ${trimmed}`);
    } catch (e) {
      toast.error(`Failed: ${e instanceof Error ? e.message : "Unknown error"}`);
    }
  };

  if (globalError || reposError) {
    return (
      <PageError
        message="Failed to load context configuration"
        onRetry={() => { refetchGlobal(); refetchRepos(); }}
      />
    );
  }

  if (globalLoading || reposLoading || !globalForm) {
    return <div className="text-muted-foreground">Loading...</div>;
  }

  return (
    <div className="space-y-6">
      {/* Global Context Injection */}
      <Card>
        <CardHeader>
          <CardTitle className="text-lg">Global Context Injection</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div>
              <Label htmlFor="system_context">System Context</Label>
              <Textarea
                id="system_context"
                value={globalForm.system_context}
                onChange={(e) => setGlobalForm((prev) => prev ? { ...prev, system_context: e.target.value } : prev)}
                placeholder="Coding standards, architectural guidelines, team conventions..."
                className="mt-1 min-h-[80px] bg-[#0c1015] border-white/[0.08]"
              />
            </div>
            <div>
              <Label htmlFor="assistant_context">Assistant Context</Label>
              <Textarea
                id="assistant_context"
                value={globalForm.assistant_context}
                onChange={(e) => setGlobalForm((prev) => prev ? { ...prev, assistant_context: e.target.value } : prev)}
                placeholder="Issue-specific instructions, prior context..."
                className="mt-1 min-h-[80px] bg-[#0c1015] border-white/[0.08]"
              />
            </div>
            <div className="flex justify-end">
              <Button onClick={handleSaveGlobal} disabled={globalSaving}>
                {globalSaving ? "Saving..." : "Save Global Context"}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Per-Repo Context */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle className="text-lg">Per-Repository Context</CardTitle>
            {!addingRepo && (
              <Button variant="outline" size="sm" onClick={() => setAddingRepo(true)}>
                <Plus className="w-4 h-4 mr-1" />
                Add Repository
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent>
          <div className="space-y-6">
            {addingRepo && (
              <div className="flex items-end gap-2 p-3 rounded-md border border-white/[0.08] bg-[#0c1015]">
                <div className="flex-1">
                  <Label htmlFor="new_repo">Repository (owner/repo)</Label>
                  <Input
                    id="new_repo"
                    value={newRepoName}
                    onChange={(e) => setNewRepoName(e.target.value)}
                    placeholder="owner/repo"
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleAddRepo();
                      if (e.key === "Escape") { setAddingRepo(false); setNewRepoName(""); }
                    }}
                  />
                </div>
                <Button size="sm" onClick={handleAddRepo}>Add</Button>
                <Button variant="outline" size="sm" onClick={() => { setAddingRepo(false); setNewRepoName(""); }}>
                  Cancel
                </Button>
              </div>
            )}

            {repoForms.length === 0 && !addingRepo && (
              <p className="text-sm text-white/40">No per-repository context configured.</p>
            )}

            {repoForms.map((repo, index) => (
              <div key={repo.repo} className="p-4 rounded-md border border-white/[0.08] space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-medium font-mono text-white/80">{repo.repo}</span>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="text-destructive hover:text-destructive"
                    onClick={() => handleDeleteRepo(repo.repo)}
                  >
                    <Trash2 className="w-4 h-4" />
                  </Button>
                </div>
                <div>
                  <Label>System Context</Label>
                  <Textarea
                    value={repo.system_context}
                    onChange={(e) => handleRepoFieldChange(index, "system_context", e.target.value)}
                    placeholder="Repository-specific system context..."
                    className="mt-1 min-h-[60px] bg-[#0c1015] border-white/[0.08]"
                  />
                </div>
                <div>
                  <Label>Assistant Context</Label>
                  <Textarea
                    value={repo.assistant_context}
                    onChange={(e) => handleRepoFieldChange(index, "assistant_context", e.target.value)}
                    placeholder="Repository-specific assistant context..."
                    className="mt-1 min-h-[60px] bg-[#0c1015] border-white/[0.08]"
                  />
                </div>
                <div className="flex justify-end">
                  <Button size="sm" onClick={() => handleSaveRepo(index)}>
                    Save
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
