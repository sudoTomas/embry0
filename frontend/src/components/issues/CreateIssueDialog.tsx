import { useMemo, useState, useEffect, useRef } from "react";
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
import { useRegisteredRepos } from "@/hooks/useRepos";
import type { IssuePriority } from "@/lib/types";
import { createFocusTrap } from "@/lib/focus-trap";
import { toastApiError } from "@/api/errorToast";

interface CreateIssueDialogProps {
  onClose: () => void;
  /** Called once the issue is created, before the dialog closes. */
  onCreated?: () => void;
  /** Open on this repository, for a first task from its page. */
  initialRepo?: string;
  labelSuggestions?: string[];
}

export function CreateIssueDialog({ onClose, onCreated, initialRepo, labelSuggestions = [] }: CreateIssueDialogProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState("");
  const [repo, setRepo] = useState(initialRepo ?? "");
  const [priority, setPriority] = useState<IssuePriority>("medium");
  const [labels, setLabels] = useState<string[]>([]);
  const [githubSync, setGithubSync] = useState(false);
  const [autoTriage, setAutoTriage] = useState(true);
  const createIssue = useCreateIssue();

  useEffect(() => {
    if (!containerRef.current) return;
    document.body.style.overflow = "hidden";
    const cleanup = createFocusTrap(containerRef.current);
    return () => {
      cleanup();
      document.body.style.overflow = "";
    };
  }, []);

  // Only registered repositories: embry0 refuses to start work on any other
  // (ERR_REPO_UNREGISTERED). The repository this dialog was opened for is
  // registered even when it is hidden from lists, so it stays on offer.
  const { data: registered, isLoading: reposLoading, isError: reposError } = useRegisteredRepos();
  const repoOptions = useMemo(() => {
    const names = new Set<string>((registered ?? []).map((r) => r.repo));
    if (initialRepo) names.add(initialRepo);
    return Array.from(names).sort();
  }, [registered, initialRepo]);
  const repoHelpText = reposError
    ? "Could not load the registered repositories."
    : reposLoading
      ? "Loading repositories…"
      : "Only repositories registered in embry0 are listed.";

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    createIssue.mutate(
      { title, body, repo: repo || null, priority, labels, github_sync_enabled: githubSync && !!repo, auto_triage: autoTriage },
      {
        onSuccess: (created) => {
          // The API answers 201 even when auto-triage could not start a job;
          // the reason rides the body, so say it rather than implying a run.
          if (created.dispatch_error) {
            toast.error(`Issue created, but no job was started: ${created.dispatch_error}`);
          } else {
            toast.success("Issue created");
          }
          onCreated?.();
          onClose();
        },
        onError: (err) => toastApiError(err, "Failed to create issue"),
      },
    );
  };

  return (
    <div
      ref={containerRef}
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
                {initialRepo && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    Pick something small and low-risk. embry0 opens a pull request for you to review. It never merges.
                  </p>
                )}
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
