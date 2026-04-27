import { useMemo, useState } from "react";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/Card";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { Label } from "@/components/ui/Label";
import { Textarea } from "@/components/ui/Textarea";
import { Select } from "@/components/ui/Select";
import { RepoSelector } from "./RepoSelector";
import { LabelInput } from "./LabelInput";
import { useCreateIssue } from "@/hooks/useIssues";
import { useGitHubRepos } from "@/hooks/useGitHub";
import type { IssuePriority } from "@/lib/types";

interface CreateIssueDialogProps {
  onClose: () => void;
  repos?: string[];
  labelSuggestions?: string[];
}

export function CreateIssueDialog({ onClose, repos = [], labelSuggestions = [] }: CreateIssueDialogProps) {
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [repo, setRepo] = useState("");
  const [priority, setPriority] = useState<IssuePriority>("medium");
  const [labels, setLabels] = useState<string[]>([]);
  const [githubSync, setGithubSync] = useState(false);
  const [autoTriage, setAutoTriage] = useState(true);
  const createIssue = useCreateIssue();

  // Pull every repo the GITHUB_TOKEN can see; merge with the locally-known
  // ones (repos that already have issues) so a user can pick from either set.
  const { data: gh, isLoading: ghLoading, isError: ghError } = useGitHubRepos();
  const repoOptions = useMemo(() => {
    const fromGitHub = gh?.repos.map((r) => r.full_name) ?? [];
    const merged = new Set<string>([...fromGitHub, ...repos]);
    return Array.from(merged).sort();
  }, [gh, repos]);
  const repoHelpText = ghError
    ? "Could not load repos from GitHub — check GITHUB_TOKEN. Falls back to repos with existing issues."
    : ghLoading
      ? "Loading repos from GitHub…"
      : `${repoOptions.length} repo${repoOptions.length === 1 ? "" : "s"} available`;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    createIssue.mutate(
      { title, body, repo: repo || null, priority, labels, github_sync_enabled: githubSync && !!repo, auto_triage: autoTriage },
      {
        onSuccess: () => { toast.success("Issue created"); onClose(); },
        onError: (err) => { toast.error(err instanceof Error ? err.message : "Failed to create issue"); },
      },
    );
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
      onClick={onClose}
      onKeyDown={(e) => e.key === "Escape" && onClose()}
      role="dialog"
      aria-modal="true"
      aria-label="Create issue dialog"
      tabIndex={-1}
    >
      <Card className="w-full max-w-2xl mx-4 max-h-[90vh] overflow-y-auto" onClick={(e) => e.stopPropagation()}>
        <CardHeader><CardTitle>Create Issue</CardTitle></CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div className="md:col-span-2">
                <Label htmlFor="issue-title">Title</Label>
                <Input id="issue-title" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Issue title" required />
              </div>
              <div className="md:col-span-2">
                <Label htmlFor="issue-body">Description</Label>
                <Textarea id="issue-body" value={body} onChange={(e) => setBody(e.target.value)} placeholder="Describe the issue (Markdown supported)" rows={4} />
              </div>
              <div>
                <Label>Repository</Label>
                <RepoSelector value={repo} onChange={setRepo} repos={repoOptions} />
                <p className="mt-1 text-xs text-muted-foreground">{repoHelpText}</p>
              </div>
              <div>
                <Label htmlFor="issue-priority">Priority</Label>
                <Select id="issue-priority" value={priority} onChange={(e) => setPriority(e.target.value as IssuePriority)}>
                  <option value="critical">Critical</option>
                  <option value="high">High</option>
                  <option value="medium">Medium</option>
                  <option value="low">Low</option>
                </Select>
              </div>
              <div className="md:col-span-2">
                <Label>Labels</Label>
                <LabelInput value={labels} onChange={setLabels} suggestions={labelSuggestions} />
              </div>
            </div>
            <div className="flex items-center gap-6 pt-2 text-sm">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={githubSync}
                  onChange={(e) => setGithubSync(e.target.checked)}
                  disabled={!repo}
                  className="rounded border-border"
                />
                <span className={!repo ? "text-muted-foreground" : ""}>Sync to GitHub</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={autoTriage}
                  onChange={(e) => setAutoTriage(e.target.checked)}
                  className="rounded border-border"
                />
                Auto-triage
              </label>
            </div>
            <div className="flex gap-2 justify-end">
              <Button type="button" variant="outline" onClick={onClose}>Cancel</Button>
              <Button type="submit" disabled={createIssue.isPending}>
                {createIssue.isPending ? "Creating..." : "Create Issue"}
              </Button>
            </div>
          </form>
        </CardContent>
      </Card>
    </div>
  );
}
